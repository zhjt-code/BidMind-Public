"""Dispatcher AIMD 自适应并发控制器

基于 AIMD（加法增-乘法减）策略，根据实时延迟、429 限流、错误率
动态调整 AIDispatcher 的全局并发上限，替代原有固定 Semaphore。

与 VL 路径的 AdaptiveConcurrency 同源但更简洁：
- 无权重概念（所有 LLM 文本请求权重=1）
- 无 is_empty 信号（LLM 文本请求不存在空响应过载特征）
- 信号来源直接从 dispatcher._dispatch 的结果采集

调整逻辑:
 增加: 窗口内全部健康 + 冷却期已过 -> +1
 Level1 减少: 尾延迟抖动 (p95 > p50 * 3) -> -2，默认禁用，见下
 Level2 减少: 窗口内存在 429/timeout -> *0.7
 Level3 减少: 连续故障 (>=3) -> *0.5

尾延迟信号的取舍:
LLM 成功请求的延迟天然重尾（输出长度差异），窗口内出现一条 40s+ 的长输出不等于
服务端过载。窗口 15 时 p95 索引 min(int(15*0.95), 14) 恰为末位，即 p95 等于窗口内
成功样本的最大值，单条长输出就能让 Level1 持续命中并清空窗口，形成"只降不升"的棘轮。
因此 Level1 默认禁用（*_TAIL_LATENCY_DECREASE_ENABLED），且禁用时增容守卫同步改用
p50 口径——否则同一条自然长尾会在增容侧继续冻结回升。拥塞的真实证据仍由
Level2/Level3 的 429/timeout/连续故障承担，判定条件与乘数保持不变。
"""

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


@dataclass
class DispatcherRequestMetric:
    """单次 Dispatcher 请求的度量数据"""

    latency_ms: float
    success: bool
    is_rate_limited: bool
    is_timeout: bool
    timestamp: float = field(default_factory=time.monotonic)


_CONSECUTIVE_FAIL_THRESHOLD = 3

# 尾延迟降档禁用后仍需保留信号可见性，但窗口满后每条请求都会判定一次，
# 逐次打印会淹没日志，故按观测次数抽样输出。
_TAIL_LATENCY_LOG_INTERVAL = 20


class _ResizableSemaphore:
    """支持欠账式缩容的 asyncio.Semaphore 包装器。

    asyncio.Semaphore 没有公开 value 属性，本类集中封装对 CPython
    `_value` 内部字段的访问，避免该实现细节继续散落在控制器中。
    """

    def __init__(self, capacity: int) -> None:
        self._semaphore = asyncio.Semaphore(capacity)
        self._pending_shrink = 0

    @property
    def _value(self) -> int:
        return self._semaphore._value

    async def acquire(self) -> bool:
        return await self._semaphore.acquire()

    def try_acquire(self) -> bool:
        """非阻塞获取; 没有立即可用的槽位就返回 False。

        公平队列需要"能不能立刻拿到"这个判断才能自己决定发给哪个项目; 若改用
        `await acquire()`, 排队顺序又会退回 Semaphore 的 FIFO, 公平轮转就失效了。
        `locked()` 为假即保证 value>0 且无排队者, 故此处扣减不会抢到他人身前。
        """
        if self._semaphore.locked():
            return False
        self._semaphore._value -= 1
        return True

    def release(self) -> None:
        if self._pending_shrink > 0:
            self._pending_shrink -= 1
            return
        self._semaphore.release()

    async def __aenter__(self) -> "_ResizableSemaphore":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    def grow(self, count: int) -> None:
        while count > 0 and self._pending_shrink > 0:
            self._pending_shrink -= 1
            count -= 1
        for _ in range(count):
            self._semaphore.release()

    def shrink(self, count: int) -> None:
        for _ in range(count):
            if self._semaphore._value > 0:
                self._semaphore._value -= 1
            else:
                self._pending_shrink += 1


class DispatcherConcurrencyController:
    """Dispatcher 专用 AIMD 自适应并发控制器

    管理一个 asyncio.Semaphore，根据滑动窗口内的请求度量
    动态调整并发容量，在吞吐量和稳定性之间自动寻找平衡点。
    """

    def __init__(
        self,
        channel_count: int,
        initial_per_channel: int,
        min_per_channel: int,
        max_per_channel: int,
        window_size: int = 15,
        increase_cooldown: int = 20,
        latency_ceiling: int = 120000,
        purpose: str = "review",
    ):
        self._channel_count = max(channel_count, 1)
        self._purpose = purpose
        self._initial = self._channel_count * initial_per_channel
        self._min_c = max(self._channel_count * min_per_channel, 1)
        self._max_c = self._channel_count * max_per_channel
        self._window_size = window_size
        self._increase_cooldown = increase_cooldown
        self._latency_ceiling = latency_ceiling

        from app.core.config import settings

        if purpose == "vision":
            self._tail_latency_decrease_enabled = bool(
                settings.VISION_CONCURRENCY_TAIL_LATENCY_DECREASE_ENABLED
            )
        else:
            self._tail_latency_decrease_enabled = bool(
                settings.REVIEW_CONCURRENCY_TAIL_LATENCY_DECREASE_ENABLED
            )
        # 增容守卫必须与降容判据同口径：尾延迟不作为拥塞证据时，守卫也不能用 p95
        self._guard_stat_key = (
            "p95" if self._tail_latency_decrease_enabled else "p50"
        )

        self._current = self._initial
        self._semaphore = _ResizableSemaphore(self._initial)
        self._metrics: deque[DispatcherRequestMetric] = deque(maxlen=self._window_size)
        self._last_increase_time = 0.0
        self._last_adjust_desc = ""
        self._prev_guard_latency: Optional[float] = None
        self._lock = asyncio.Lock()
        self._total_adjustments_up = 0
        self._total_adjustments_down = 0
        self._tail_latency_observed = 0

        logger.info(
            f"DispatcherConcurrencyController 初始化: "
            f"purpose={self._purpose}, channels={self._channel_count}, "
            f"initial={self._initial}, min={self._min_c}, max={self._max_c}, "
            f"window={self._window_size}, ceiling={self._latency_ceiling}ms, "
            f"tail_latency_decrease={self._tail_latency_decrease_enabled}"
        )

    @property
    def current(self) -> int:
        return self._current

    @property
    def semaphore(self) -> _ResizableSemaphore:
        return self._semaphore

    async def record(self, metric: DispatcherRequestMetric) -> None:
        """记录一次请求度量并触发自适应调整检查"""
        self._metrics.append(metric)
        if len(self._metrics) >= self._window_size:
            await self._maybe_adjust()

    async def _maybe_adjust(self) -> None:
        async with self._lock:
            if len(self._metrics) < self._window_size:
                return

            metrics = list(self._metrics)
            stats = self._compute_window_stats(metrics)

            new_c = self._current
            desc = ""

            tail_latency_hit = (
                stats["p50"] > 0
                and stats["p95"] > stats["p50"] * 3
            )
            if tail_latency_hit:
                self._tail_latency_observed += 1
                if (
                    not self._tail_latency_decrease_enabled
                    and self._tail_latency_observed % _TAIL_LATENCY_LOG_INTERVAL == 1
                ):
                    logger.info(
                        f"[Dispatcher自适应并发] 尾延迟观测(降档已禁用, 不调整) "
                        f"purpose={self._purpose}, current={self._current}, "
                        f"p50={stats['p50']:.0f}ms, p95={stats['p95']:.0f}ms, "
                        f"累计观测={self._tail_latency_observed}"
                    )

            consecutive_fail = self._count_consecutive_tail_failures(metrics)
            if consecutive_fail >= _CONSECUTIVE_FAIL_THRESHOLD:
                new_c = max(self._min_c, math.floor(self._current * 0.5))
                desc = f"consecutive_fail({consecutive_fail}) *0.5 -> {new_c}"

            elif stats["rate_limited_count"] > 0 or stats["timeout_count"] > 0:
                new_c = max(self._min_c, math.floor(self._current * 0.7))
                trigger = "rate_limited" if stats["rate_limited_count"] > 0 else "timeout"
                desc = f"{trigger} *0.7 -> {new_c}"

            elif tail_latency_hit and self._tail_latency_decrease_enabled:
                new_c = max(self._min_c, self._current - 2)
                desc = (
                    f"tail_latency(p95={stats['p95']:.0f}>"
                    f"p50*3={stats['p50']*3:.0f}) -2 -> {new_c}"
                )

            elif self._should_increase(stats):
                new_c = min(self._max_c, self._current + 1)
                desc = f"stable +1 -> {new_c}"
                self._last_increase_time = time.monotonic()

            if new_c != self._current:
                old_c = self._current
                self._current = new_c
                self._resize_semaphore(new_c, old_c)
                self._last_adjust_desc = desc

                if new_c < old_c:
                    self._metrics.clear()
                    self._total_adjustments_down += 1
                    direction = "down"
                else:
                    self._total_adjustments_up += 1
                    direction = "up"

                self._prev_guard_latency = stats[self._guard_stat_key]
                logger.info(
                    f"[Dispatcher自适应并发] {desc} "
                    f"(p50={stats['p50']:.0f}ms, p95={stats['p95']:.0f}ms, "
                    f"429={stats['rate_limited_count']}, "
                    f"err={stats['error_rate']:.0%})"
                )

                try:
                    from app.core.ai.metrics import record_concurrency_adjustment
                    record_concurrency_adjustment(self._purpose, direction, new_c)
                except Exception:
                    pass

    def _resize_semaphore(self, new_capacity: int, old_capacity: int) -> None:
        """调整 Semaphore 容量

        asyncio.Semaphore 不支持直接 resize，通过增减 release/acquire 模拟。
        增大时：释放差额（增加可用槽位）
        减小时：获取差额（减少可用槽位，不阻塞已占用的）
        """
        diff = new_capacity - old_capacity
        if diff > 0:
            self._semaphore.grow(diff)
        elif diff < 0:
            self._semaphore.shrink(-diff)

    def _should_increase(self, stats: dict) -> bool:
        if self._current >= self._max_c:
            return False
        if stats["timeout_count"] > 0:
            return False
        if stats["rate_limited_count"] > 0:
            return False
        if stats["error_rate"] > 0:
            return False
        guard_latency = stats[self._guard_stat_key]
        if guard_latency > self._latency_ceiling:
            return False
        if (
            self._prev_guard_latency is not None
            and self._prev_guard_latency > 0
            and guard_latency > self._prev_guard_latency * 1.3
        ):
            return False
        elapsed = time.monotonic() - self._last_increase_time
        if elapsed < self._increase_cooldown:
            return False
        return True

    @staticmethod
    def _compute_window_stats(metrics: list[DispatcherRequestMetric]) -> dict:
        total = len(metrics)
        if total == 0:
            return {
                "p50": 0, "p95": 0,
                "timeout_count": 0, "rate_limited_count": 0,
                "error_rate": 0.0,
            }

        timeout_count = sum(1 for m in metrics if m.is_timeout)
        rate_limited_count = sum(1 for m in metrics if m.is_rate_limited)
        fail_count = sum(1 for m in metrics if not m.success)

        success_latencies = sorted(
            m.latency_ms for m in metrics if m.success
        )
        if success_latencies:
            p50 = success_latencies[len(success_latencies) // 2]
            p95_idx = min(
                int(len(success_latencies) * 0.95),
                len(success_latencies) - 1,
            )
            p95 = success_latencies[p95_idx]
        else:
            p50 = 0
            p95 = 0

        return {
            "p50": p50,
            "p95": p95,
            "timeout_count": timeout_count,
            "rate_limited_count": rate_limited_count,
            "error_rate": fail_count / total,
        }

    @staticmethod
    def _count_consecutive_tail_failures(
        metrics: list[DispatcherRequestMetric],
    ) -> int:
        """从窗口尾部向前计数连续的 timeout 或 429"""
        count = 0
        for m in reversed(metrics):
            if m.is_timeout or m.is_rate_limited:
                count += 1
            else:
                break
        return count

    def get_stats(self) -> dict:
        """返回当前统计信息，供状态 API 使用"""
        metrics = list(self._metrics)
        stats = self._compute_window_stats(metrics)
        return {
            "current": self._current,
            "initial": self._initial,
            "min": self._min_c,
            "max": self._max_c,
            "p50_ms": int(stats["p50"]),
            "p95_ms": int(stats["p95"]),
            "rate_limited_count": stats["rate_limited_count"],
            "error_rate": round(stats["error_rate"], 2),
            "last_adjust": self._last_adjust_desc,
            "window_fill": len(self._metrics),
            "total_adjustments_up": self._total_adjustments_up,
            "total_adjustments_down": self._total_adjustments_down,
            "tail_latency_decrease_enabled": self._tail_latency_decrease_enabled,
            "tail_latency_observed": self._tail_latency_observed,
        }
