"""Dispatcher 请求许可的项目级公平发放

职责: 按 project_id 以 DRR（Deficit Round Robin）轮转把并发容量发给争用中的各个项目
不做: 不决定 worker job 能否执行、不做跨进程协调、不调整 AIMD 容量上限、不发起 LLM 请求

为什么 job 级公平还不够:
 单个 job 内部会一次扇出几十个 LLM 请求（写标分组并行、审查 as_completed、串标交叉
 对比等）。这些请求全部压在 dispatcher 的同一个 Semaphore 上, 而 asyncio.Semaphore
 是严格 FIFO: A 项目先排进 50 个请求, B 项目的第一个请求就得等在第 51 位。于是
 "项目租约"保证了 B 的 job 能开跑, B 的 job 却依然在 LLM 这一层被 A 饿死。

轮转如何保证工作保持:
 没有排队者时走快路径直接取空闲槽位, 单项目场景不引入任何额外调度延迟;
 轮转队列只登记"当前有请求在排队"的项目, 项目一旦排空就出队并把配额清零 ——
 空闲项目不累积配额, 它腾出的份额自然被仍有需求的项目借走。

容量的唯一真源仍是 AIMD 控制器的 Semaphore:
 本模块只决定"下一个许可发给谁", 从不自己造许可, 因此 AIMD 的动态增减容量、
 欠账式缩容都照常生效。
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from weakref import WeakKeyDictionary

from loguru import logger

from app.core.config import settings

# 每个请求消耗的配额。与 quantum 同量级时 DRR 退化为纯轮转, 这正是"每个项目轮流拿一个
# 许可"的期望行为; 保留 deficit 结构是为了将来能给不同项目配不同权重。
_REQUEST_COST = 1.0

_NO_PROJECT = "none"


@dataclass
class _ProjectFlow:
    """单个项目在本 dispatcher 上的排队与配额状态。"""

    key: str
    deficit: float = 0.0
    granted: int = 0
    waiters: deque[asyncio.Future] = field(default_factory=deque)


class ProjectFairQueue:
    """把容量源的许可按项目轮转发放。

    `source` 只需提供 `try_acquire() -> bool` 与 `release()`, 因此既能接
    `_ResizableSemaphore`, 也能在测试里接任意假容量源。
    """

    def __init__(self, source: object, *, quantum: Optional[float] = None) -> None:
        self._source = source
        self._flows: dict[str, _ProjectFlow] = {}
        self._round: deque[str] = deque()
        self._pending = 0
        configured = (
            getattr(settings, "AI_PROJECT_FAIR_QUEUE_QUANTUM", 1.0)
            if quantum is None
            else quantum
        )
        # quantum 必须为正, 否则补配额的循环永远无法让任何项目达到可发放门槛
        self._quantum = max(float(configured), 0.01)

    @property
    def pending(self) -> int:
        return self._pending

    async def acquire_slot(self, key: str) -> bool:
        """取得一个请求许可; 有其它项目在排队时按轮转等待。"""

        flow = self._flows.get(key)
        if flow is None:
            flow = _ProjectFlow(key)
            self._flows[key] = flow

        if self._pending == 0 and self._try_take(flow):
            return True

        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        flow.waiters.append(waiter)
        self._pending += 1
        if key not in self._round:
            self._round.append(key)

        try:
            await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                # 许可已经发出却被外层取消（dispatcher 的 deadline wait_for 会这样做），
                # 必须原样归还, 否则每超时一次就永久漏掉一个并发槽位。
                self.release_slot(key)
            else:
                self._discard_waiter(flow, waiter)
            raise
        return True

    def release_slot(self, key: str) -> None:
        """归还许可, 并立刻把腾出的容量按轮转发给下一个项目。"""

        flow = self._flows.get(key)
        if flow is not None and flow.granted > 0:
            flow.granted -= 1
        self._source.release()  # type: ignore[attr-defined]
        self._grant_pending()

    def snapshot(self) -> dict:
        """供诊断使用的轻量快照。"""

        return {
            "pending": self._pending,
            "round": list(self._round),
            "granted": {k: f.granted for k, f in self._flows.items() if f.granted},
            "queued": {k: len(f.waiters) for k, f in self._flows.items() if f.waiters},
        }

    def _try_take(self, flow: _ProjectFlow) -> bool:
        if not self._source.try_acquire():  # type: ignore[attr-defined]
            return False
        flow.granted += 1
        return True

    def _discard_waiter(self, flow: _ProjectFlow, waiter: asyncio.Future) -> None:
        try:
            flow.waiters.remove(waiter)
        except ValueError:
            return
        self._pending -= 1

    def _grant_pending(self) -> None:
        while self._pending > 0:
            # 必须先确认真的有容量再去轮转: 反过来的话, 轮到某项目时若恰好没有容量,
            # 它的这一轮配额就白白扣掉了, 下次腾出容量时又轮回上一个项目 —— 积压多的
            # 项目会因此连续拿到发放, 公平轮转形同虚设。
            if not self._source.try_acquire():  # type: ignore[attr-defined]
                return
            flow = self._next_flow()
            if flow is None:
                self._source.release()  # type: ignore[attr-defined]
                return
            waiter = flow.waiters.popleft()
            self._pending -= 1
            if waiter.cancelled():
                self._source.release()  # type: ignore[attr-defined]
                continue
            flow.granted += 1
            waiter.set_result(True)

    def _next_flow(self) -> Optional[_ProjectFlow]:
        """DRR: 轮转到下一个仍有请求排队且配额足够的项目。"""

        # 每轮最多给每个项目补一次配额; 补满一圈后必定有项目达到门槛。
        budget = 2 * len(self._round) + 2
        while self._round and budget > 0:
            budget -= 1
            key = self._round[0]
            flow = self._flows.get(key)
            if flow is None or not flow.waiters:
                self._round.popleft()
                if flow is not None:
                    # 排空即清零: 否则项目空闲一段时间后回来能一次性抢走一大把许可
                    flow.deficit = 0.0
                continue
            if flow.deficit < _REQUEST_COST:
                flow.deficit += self._quantum
                self._round.rotate(-1)
                continue
            flow.deficit -= _REQUEST_COST
            self._round.rotate(-1)
            return flow
        return None


class _FairSlot:
    """单次请求的许可句柄, 接口与 Semaphore 一致以便 dispatcher 原样调用。"""

    __slots__ = ("_queue", "_key")

    def __init__(self, queue: ProjectFairQueue, key: str) -> None:
        self._queue = queue
        self._key = key

    async def acquire(self) -> bool:
        return await self._queue.acquire_slot(self._key)

    def release(self) -> None:
        self._queue.release_slot(self._key)


# 公平队列的生命周期跟随容量源: AIMD 控制器被重建时会换一个新的 Semaphore,
# 旧队列随之被回收, 不会把上一代的排队状态带到新容量上。
_queues: "WeakKeyDictionary[object, ProjectFairQueue]" = WeakKeyDictionary()


def queue_for_source(source: object) -> ProjectFairQueue:
    """取得（必要时创建）某个容量源对应的公平队列。"""

    queue = _queues.get(source)
    if queue is None:
        queue = ProjectFairQueue(source)
        _queues[source] = queue
    return queue


def fair_queue_enabled() -> bool:
    return bool(getattr(settings, "AI_PROJECT_FAIR_QUEUE_ENABLED", True))


def fair_request_slot(source: object, *, purpose: str, project_id: object) -> object:
    """把裸容量源包装成按项目轮转发放的许可句柄。

    开关关闭、或容量源不支持非阻塞获取时原样返回, 行为完全退回 FIFO Semaphore。
    """

    if not fair_queue_enabled() or not hasattr(source, "try_acquire"):
        return source
    try:
        queue = queue_for_source(source)
    except TypeError:
        # 容量源不可弱引用时不强行接管, 宁可退回 FIFO 也不引入内存泄漏
        logger.debug(f"容量源不支持公平队列托管, 退回 FIFO: purpose={purpose}")
        return source
    return _FairSlot(queue, _flow_key(purpose, project_id))


def _flow_key(purpose: str, project_id: object) -> str:
    text = "" if project_id is None else str(project_id).strip()
    return f"{purpose}:{text or _NO_PROJECT}"
