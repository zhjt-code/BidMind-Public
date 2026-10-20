"""单 job 内部扇出上界测试

写标 compose、审查 as_completed、串标交叉对比这些路径都是"一个 job 内部一次性
create_task 出几十个 LLM 请求", 本身没有内建上限, 唯一的闸门就是 dispatcher 的并发
容量。这里用真实的 AIDispatcher + 假通道跑, 锁定的风险:
- 单个 job 的扇出突破 dispatcher 并发上限, 把下游打爆
- 一个项目的大扇出把其它项目的请求压在 FIFO 队尾, 直到扇出跑完才轮到
- 扇出请求被取消后并发槽位泄漏, 容量越跑越小
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.dispatcher import AIDispatcher


class _TrackingClient:
    """记录在途并发峰值与获得许可顺序的假通道。"""

    base_url = "https://example.com/v1"
    model_name = "test-model"

    def __init__(self, hold_seconds: float = 0.01) -> None:
        self.hold_seconds = hold_seconds
        self.in_flight = 0
        self.peak_in_flight = 0
        self.start_order: list[str] = []

    async def chat(self, user_message: str = "", **kwargs: object) -> str:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        self.start_order.append(user_message)
        try:
            await asyncio.sleep(self.hold_seconds)
            return "ok"
        finally:
            self.in_flight -= 1


_PINNED_CAPACITY = 4


def _make_dispatcher(
    monkeypatch, hold_seconds: float = 0.01
) -> tuple[AIDispatcher, _TrackingClient, int]:
    """构造 dispatcher 并把 AIMD 容量钉死, 否则自适应增容会让"上界"随跑随变。"""

    from app.core.config import settings

    monkeypatch.setattr(settings, "REVIEW_CONCURRENCY_CLOUD_INITIAL", _PINNED_CAPACITY)
    monkeypatch.setattr(settings, "REVIEW_CONCURRENCY_CLOUD_MIN", 1)
    monkeypatch.setattr(settings, "REVIEW_CONCURRENCY_CLOUD_MAX", _PINNED_CAPACITY)

    client = _TrackingClient(hold_seconds)
    dispatcher = AIDispatcher(purpose="review")
    dispatcher.register_channel(client, config_id="c1")
    capacity = dispatcher._get_controller().current
    assert capacity == _PINNED_CAPACITY
    return dispatcher, client, capacity


async def _settle() -> None:
    for _ in range(8):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_single_job_fanout_never_exceeds_dispatcher_capacity(monkeypatch) -> None:
    """一个 job 一次扇出 30 个请求, 在途并发不得越过 dispatcher 容量。"""

    dispatcher, client, capacity = _make_dispatcher(monkeypatch)

    await asyncio.gather(*[
        dispatcher._dispatch("chat", project_id=1, user_message=f"a{index}")
        for index in range(30)
    ])

    assert client.peak_in_flight <= capacity, (
        f"单 job 扇出峰值 {client.peak_in_flight} 超过并发上限 {capacity}"
    )
    assert len(client.start_order) == 30, "所有扇出请求最终都必须跑完"


@pytest.mark.asyncio
async def test_grouped_fanout_of_one_project_stays_within_capacity(monkeypatch) -> None:
    """复刻 compose 的分组并行形态: 多组各自串行, 组间并行, 总量仍受容量约束。"""

    dispatcher, client, capacity = _make_dispatcher(monkeypatch)

    async def _compose_group(group: int) -> None:
        for section in range(4):
            await dispatcher._dispatch(
                "chat", project_id=7, user_message=f"g{group}-s{section}"
            )

    await asyncio.gather(*[_compose_group(group) for group in range(6)])

    assert client.peak_in_flight <= capacity, (
        f"分组并行扇出峰值 {client.peak_in_flight} 超过并发上限 {capacity}"
    )
    assert len(client.start_order) == 24


@pytest.mark.asyncio
async def test_other_project_is_not_stuck_behind_a_large_fanout(monkeypatch) -> None:
    """ 请求级本体: B 的单个请求不得排在 A 的 30 个扇出之后。"""

    dispatcher, client, capacity = _make_dispatcher(monkeypatch, hold_seconds=0.02)

    fanout = [
        asyncio.create_task(
            dispatcher._dispatch("chat", project_id=1, user_message=f"a{index}")
        )
        for index in range(30)
    ]
    await _settle()  # 让 A 的扇出先把队列占满, 复刻"B 后到"的真实时序

    latecomer = asyncio.create_task(
        dispatcher._dispatch("chat", project_id=2, user_message="b0")
    )
    await asyncio.gather(*fanout, latecomer)

    position = client.start_order.index("b0")
    assert position <= capacity + 2, (
        f"B 的请求排在第 {position + 1} 位才开跑（容量 {capacity}）, "
        f"说明它被 A 的扇出压在了队尾: {client.start_order[:10]}"
    )


@pytest.mark.asyncio
async def test_five_projects_share_the_fanout_capacity(monkeypatch) -> None:
    """5 个项目同时扇出时, 每个项目都必须在头几轮里拿到许可。"""

    dispatcher, client, capacity = _make_dispatcher(monkeypatch, hold_seconds=0.02)

    tasks = [
        asyncio.create_task(
            dispatcher._dispatch("chat", project_id=1, user_message=f"a{index}")
        )
        for index in range(20)
    ]
    await _settle()
    for project in (2, 3, 4, 5):
        tasks.append(
            asyncio.create_task(
                dispatcher._dispatch(
                    "chat", project_id=project, user_message=f"p{project}"
                )
            )
        )
    await asyncio.gather(*tasks)

    positions = {
        f"p{project}": client.start_order.index(f"p{project}")
        for project in (2, 3, 4, 5)
    }
    assert max(positions.values()) <= capacity + 8, (
        f"有项目被 A 的扇出长时间挡在后面: {positions}"
    )


@pytest.mark.asyncio
async def test_cancelled_fanout_request_does_not_shrink_capacity(monkeypatch) -> None:
    """扇出请求被取消后容量必须完好, 否则每取消一次就永久少一个槽位。"""

    dispatcher, client, capacity = _make_dispatcher(monkeypatch, hold_seconds=0.05)

    blockers = [
        asyncio.create_task(
            dispatcher._dispatch("chat", project_id=1, user_message=f"hold{index}")
        )
        for index in range(capacity)
    ]
    await _settle()

    queued = asyncio.create_task(
        dispatcher._dispatch("chat", project_id=2, user_message="cancelled")
    )
    await _settle()
    queued.cancel()
    await asyncio.gather(*blockers, queued, return_exceptions=True)

    client.peak_in_flight = 0
    await asyncio.gather(*[
        dispatcher._dispatch("chat", project_id=3, user_message=f"after{index}")
        for index in range(capacity * 2)
    ])

    assert client.peak_in_flight == capacity, (
        f"取消后可用容量变成 {client.peak_in_flight}, 期望仍为 {capacity}"
    )
