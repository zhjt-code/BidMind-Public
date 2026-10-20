"""VL图片识别自适应并发控制器

基于AIMD(加法增-乘法减)策略, 根据实时延迟、错误率、空响应率
动态调整VL识别并发数, 避免硬编码带来的资源浪费或服务端过载。

度量信号:
  - latency (p50/p95): 服务端处理能力指标
  - empty_rate: VL模型过载时返回空而非报错的特有信号
  - timeout/rate_limited: 显式的过载信号
  - p95 > p50 * 2.5: 尾延迟抖动, 表示服务端排队

调整逻辑:
  增加: 窗口内全部健康 + 冷却期已过 → +1
  Level1减少: 尾延迟抖动或轻度空响应 → -2
  Level2减少: timeout/429/高空响应率 → *0.7
  Level3减少: 连续故障 → *0.5
"""

import asyncio
import math
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


@dataclass
class RequestMetric:
    """单次VL请求的度量数据"""

    latency_ms: float
    success: bool
    is_empty: bool
    is_timeout: bool
    is_rate_limited: bool
    weight: int  # 1=逐图, 2=整页
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class OcrRequestMetric:
    """单次 OCR 请求的度量数据"""

    latency_ms: float
    queue_wait_ms: float
    success: bool
    is_timeout: bool
    is_transport_error: bool
    is_low_confidence: bool
    weight: int
    request_class: str = "per_image"
    timestamp: float = field(default_factory=time.monotonic)


class WeightedAdaptiveSemaphore:
    """支持变量权重和动态容量调整的信号量

    标准 asyncio.Semaphore 不支持:
    1. 单次 acquire 消耗多个槽位(权重)
    2. 运行时 resize
    本类基于 asyncio.Condition 实现这两个能力。
    """

    def __init__(self, capacity: int):
        self._capacity = capacity
        self._used = 0
        self._condition = asyncio.Condition()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def used(self) -> int:
        return self._used

    async def acquire(self, weight: int = 1) -> None:
        async with self._condition:
            while self._used + weight > self._capacity:
                await self._condition.wait()
            self._used += weight

    async def release(self, weight: int = 1) -> None:
        async with self._condition:
            self._used = max(0, self._used - weight)
            self._condition.notify_all()

    def resize(self, new_capacity: int) -> None:
        """动态调整容量, 由控制器在调整并发时调用。

        若容量增大, 需要唤醒可能在等待的协程。
        使用 call_soon_threadsafe 兼容从非事件循环线程调用的场景。
        """
        old = self._capacity
        self._capacity = new_capacity
        if new_capacity > old:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._notify_waiters())
            except RuntimeError:
                pass

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    @asynccontextmanager
    async def weighted(self, weight: int = 1):
        """异步上下文管理器, 自动 acquire/release 指定权重"""
        await self.acquire(weight)
        try:
            yield
        finally:
            await self.release(weight)


# 连续故障检测的尾部扫描长度
_CONSECUTIVE_FAIL_THRESHOLD = 3


class AdaptiveConcurrency:
    """AIMD自适应并发控制器

    根据滑动窗口内的请求度量动态调整VL识别并发数。
    整页VL和逐图VL均使用 weight=1, 共享同一控制器。

    使用方式:
        controller = AdaptiveConcurrency(config_type="cloud", channel_count=5)

        # 整页VL
        async with controller.weighted(weight=1):
            result = await analyze_full_page(...)
        await controller.record(metric)

        # 逐图VL (在 analyze_images 内部)
        async with controller.weighted(weight=1):
            result = await analyze_single_image(...)
        await controller.record(metric)

        # 轮次切换时重置指标窗口, 保留已学习的并发数
        controller.reset_window()
    """

    def __init__(self, config_type: str = "cloud", channel_count: int = 1):
        """初始化自适应并发控制器

        Args:
            config_type: 模型部署类型 ("cloud" / "local")
            channel_count: 当前使用模型组的 key 数量,
                并发参数按 channel_count * per_key 缩放,
                保证并发基数与实际可用 key 资源一致。
        """
        from app.core.config import settings

        ch = max(channel_count, 1)

        if config_type == "cloud":
            self._initial = ch * settings.VL_CONCURRENCY_CLOUD_INITIAL
            self._min_c = max(ch * settings.VL_CONCURRENCY_CLOUD_MIN, 1)
            self._max_c = ch * settings.VL_CONCURRENCY_CLOUD_MAX
            self._latency_ceiling = settings.VL_CONCURRENCY_LATENCY_CEILING_CLOUD
        else:
            self._initial = ch * settings.VL_CONCURRENCY_LOCAL_INITIAL
            self._min_c = max(ch * settings.VL_CONCURRENCY_LOCAL_MIN, 1)
            self._max_c = ch * settings.VL_CONCURRENCY_LOCAL_MAX
            self._latency_ceiling = settings.VL_CONCURRENCY_LATENCY_CEILING_LOCAL

        self._window_size = settings.VL_CONCURRENCY_WINDOW_SIZE
        self._increase_cooldown = settings.VL_CONCURRENCY_INCREASE_COOLDOWN

        self._current = self._initial
        self._semaphore = WeightedAdaptiveSemaphore(self._initial)
        self._metrics: deque[RequestMetric] = deque(maxlen=self._window_size)
        self._last_increase_time = 0.0
        self._last_adjust_desc = ""
        self._prev_p95: Optional[float] = None
        self._lock = asyncio.Lock()

        logger.info(
            f"AdaptiveConcurrency 初始化: type={config_type}, channels={ch}, "
            f"initial={self._initial}, min={self._min_c}, max={self._max_c}, "
            f"window={self._window_size}, ceiling={self._latency_ceiling}ms"
        )

    @property
    def current(self) -> int:
        return self._current

    # ── 信号量代理 ──────────────────────────────────────────────

    async def acquire(self, weight: int = 1) -> None:
        await self._semaphore.acquire(weight)

    async def release(self, weight: int = 1) -> None:
        await self._semaphore.release(weight)

    @asynccontextmanager
    async def weighted(self, weight: int = 1):
        async with self._semaphore.weighted(weight):
            yield

    # ── 轮次管理 ──────────────────────────────────────────────

    def reset_window(self) -> None:
        """重置指标窗口, 保留当前并发数

        用于VL识别轮次切换: 上一轮的延迟指标不适用于新轮次,
        但并发数是上一轮自适应学习的结果, 应保留。
        """
        self._metrics.clear()
        self._prev_p95 = None
        self._last_increase_time = 0.0
        logger.info(
            f"[自适应并发] 窗口重置: 保留并发={self._current}, "
            f"容量范围=[{self._min_c}, {self._max_c}]"
        )

    # ── 度量采集与调整 ──────────────────────────────────────────

    async def record(self, metric: RequestMetric) -> None:
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

            # -- Level 3: 连续故障(最高优先级) --
            consecutive_fail = self._count_consecutive_tail_failures(metrics)
            if consecutive_fail >= _CONSECUTIVE_FAIL_THRESHOLD:
                new_c = max(self._min_c, math.floor(self._current * 0.5))
                desc = f"consecutive_fail({consecutive_fail}) *0.5 -> {new_c}"

            # -- Level 2: timeout/429/高空响应率 --
            elif stats["timeout_count"] > 0 or stats["rate_limited_count"] > 0:
                new_c = max(self._min_c, math.floor(self._current * 0.7))
                trigger = "timeout" if stats["timeout_count"] > 0 else "rate_limited"
                desc = f"{trigger} *0.7 -> {new_c}"
            elif stats["empty_rate"] > 0.30:
                new_c = max(self._min_c, math.floor(self._current * 0.7))
                desc = f"empty_rate({stats['empty_rate']:.0%}) *0.7 -> {new_c}"

            # -- Level 1: 尾延迟抖动/轻度空响应 --
            elif (
                stats["p50"] > 0
                and stats["p95"] > stats["p50"] * 2.5
            ):
                new_c = max(self._min_c, self._current - 2)
                desc = f"tail_latency(p95={stats['p95']:.0f}>p50*2.5={stats['p50']*2.5:.0f}) -2 -> {new_c}"
            elif stats["empty_rate"] > 0.15:
                new_c = max(self._min_c, self._current - 2)
                desc = f"empty_rate({stats['empty_rate']:.0%}) -2 -> {new_c}"

            # -- 增加: 全部健康 + 冷却期已过 --
            elif self._should_increase(stats):
                new_c = min(self._max_c, self._current + 1)
                desc = f"stable +1 -> {new_c}"
                self._last_increase_time = time.monotonic()

            if new_c != self._current:
                old_c = self._current
                self._current = new_c
                self._semaphore.resize(new_c)
                self._last_adjust_desc = desc

                if new_c < old_c:
                    self._metrics.clear()

                self._prev_p95 = stats["p95"]
                logger.info(
                    f"[自适应并发] {desc} "
                    f"(p50={stats['p50']:.0f}ms, p95={stats['p95']:.0f}ms, "
                    f"empty={stats['empty_rate']:.0%}, "
                    f"err={stats['error_rate']:.0%})"
                )

    def _should_increase(self, stats: dict) -> bool:
        if self._current >= self._max_c:
            return False
        if stats["timeout_count"] > 0:
            return False
        if stats["rate_limited_count"] > 0:
            return False
        if stats["empty_rate"] >= 0.05:
            return False
        if stats["error_rate"] > 0:
            return False
        if stats["p95"] > self._latency_ceiling:
            return False
        if (
            self._prev_p95 is not None
            and self._prev_p95 > 0
            and stats["p95"] > self._prev_p95 * 1.3
        ):
            return False
        elapsed = time.monotonic() - self._last_increase_time
        if elapsed < self._increase_cooldown:
            return False
        return True

    @staticmethod
    def _compute_window_stats(metrics: list[RequestMetric]) -> dict:
        total = len(metrics)
        if total == 0:
            return {
                "p50": 0, "p95": 0,
                "timeout_count": 0, "rate_limited_count": 0,
                "empty_rate": 0.0, "error_rate": 0.0,
            }

        timeout_count = sum(1 for m in metrics if m.is_timeout)
        rate_limited_count = sum(1 for m in metrics if m.is_rate_limited)
        empty_count = sum(1 for m in metrics if m.is_empty and m.success)
        fail_count = sum(1 for m in metrics if not m.success)

        success_latencies = sorted(
            m.latency_ms for m in metrics if m.success and not m.is_empty
        )
        if success_latencies:
            p50 = success_latencies[len(success_latencies) // 2]
            p95_idx = min(int(len(success_latencies) * 0.95), len(success_latencies) - 1)
            p95 = success_latencies[p95_idx]
        else:
            p50 = 0
            p95 = 0

        return {
            "p50": p50,
            "p95": p95,
            "timeout_count": timeout_count,
            "rate_limited_count": rate_limited_count,
            "empty_rate": empty_count / total,
            "error_rate": fail_count / total,
        }

    @staticmethod
    def _count_consecutive_tail_failures(metrics: list[RequestMetric]) -> int:
        """从窗口尾部向前计数连续的 timeout 或空响应"""
        count = 0
        for m in reversed(metrics):
            if m.is_timeout or (m.success and m.is_empty):
                count += 1
            else:
                break
        return count

    # ── 可观测性 ──────────────────────────────────────────────

    def get_stats(self) -> dict:
        """返回当前统计信息, 供心跳上报使用"""
        metrics = list(self._metrics)
        stats = self._compute_window_stats(metrics)
        return {
            "concurrency": self._current,
            "p95_ms": int(stats["p95"]),
            "p50_ms": int(stats["p50"]),
            "empty_rate": round(stats["empty_rate"], 2),
            "error_rate": round(stats["error_rate"], 2),
            "last_adjust": self._last_adjust_desc,
            "window_fill": len(self._metrics),
            "semaphore_used": self._semaphore.used,
        }


class OcrAdaptiveConcurrency:
    """OCR 专用 AIMD 自适应并发控制器

    与 VL 控制器同源，但信号更贴近 OCR sidecar:
    - `queue_wait_ms`: 客户端等待信号量的排队时间
    - `latency_ms`: HTTP 请求真实执行耗时
    - `is_timeout` / `is_transport_error`: sidecar 过载或链路失败
    - `is_low_confidence`: 请求成功但 OCR 质量偏低
    """

    def __init__(self, instance_count: int = 1) -> None:
        from app.core.config import settings

        n = max(instance_count, 1)
        self._instance_count = n
        self._initial = max(settings.OCR_CONCURRENCY_INITIAL * n, 1)
        self._min_c = max(settings.OCR_CONCURRENCY_MIN * n, 1)
        self._max_c = max(settings.OCR_CONCURRENCY_MAX * n, self._min_c)
        self._window_size = max(settings.OCR_CONCURRENCY_WINDOW_SIZE, 4)
        self._increase_cooldown = max(
            settings.OCR_CONCURRENCY_INCREASE_COOLDOWN, 1
        )
        self._latency_ceiling = max(
            settings.OCR_CONCURRENCY_LATENCY_CEILING_MS, 1000
        )
        self._queue_wait_ceiling = max(
            settings.OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS, 1000
        )

        self._current = min(max(self._initial, self._min_c), self._max_c)
        self._semaphore = WeightedAdaptiveSemaphore(self._current)
        self._metrics: deque[OcrRequestMetric] = deque(maxlen=self._window_size)
        self._last_increase_time = 0.0
        self._last_adjust_desc = ""
        self._prev_p95: Optional[float] = None
        self._prev_queue_p95: Optional[float] = None
        # 记录 _prev_queue_p95 最近一次写入时间，供陈旧度护栏判定使用
        self._prev_queue_p95_updated_at: Optional[float] = None
        self._lock = asyncio.Lock()

        # OCR 恢复抢占: 拥堵解除时通知等待者
        self._congestion_cleared = asyncio.Event()
        self._congestion_cleared.set()  # 初始状态非拥堵
        self._was_congested = False

        logger.info(
            f"OcrAdaptiveConcurrency 初始化: initial={self._current}, "
            f"min={self._min_c}, max={self._max_c}, window={self._window_size}, "
            f"latency_ceiling={self._latency_ceiling}ms, "
            f"queue_wait_ceiling={self._queue_wait_ceiling}ms"
        )

    @property
    def current(self) -> int:
        return self._current

    _FAST_RECOVERY_SAMPLE = 3

    @property
    def is_congested(self) -> bool:
        """基于历史排队 p95 判断 OCR 是否拥堵，供路由降级决策使用

 判定顺序（任一非拥堵条件命中即返回 False）:
 1. 基础短路: 历史 p95 从未超阈值
 2. 空窗口短路: 信号量空闲 + 度量窗口已空，
 说明历史拥堵信号已无现实依据，避免"无新请求 → 永远拥堵"自死锁
 3. 陈旧度护栏: _prev_queue_p95 长时间未更新视为失效，
 防止单次历史尖峰把 OCR 永久钉死在降级状态
 4. 既有快速恢复: 信号量空闲 + 最近 N 条样本排队远低于阈值
 """
        from app.core.config import settings
        threshold = settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS
        if self._prev_queue_p95 is None or self._prev_queue_p95 < threshold:
            return False

        if self._semaphore.used == 0 and len(self._metrics) == 0:
            return False

        if self._prev_queue_p95_updated_at is not None:
            stale_sec = settings.OCR_CONGESTION_STALE_SEC
            age = time.monotonic() - self._prev_queue_p95_updated_at
            if stale_sec > 0 and age > stale_sec:
                return False

        if (
            self._semaphore.used == 0
            and len(self._metrics) >= self._FAST_RECOVERY_SAMPLE
        ):
            recent = list(self._metrics)[-self._FAST_RECOVERY_SAMPLE:]
            if all(m.queue_wait_ms < threshold * 0.5 for m in recent):
                return False
        return True

    async def wait_for_recovery(self, timeout: float = 3.0) -> bool:
        """等待 OCR 拥堵恢复，供竞争路由使用

        Returns:
            True 表示 OCR 已恢复或本就不拥堵，False 表示超时仍拥堵
        """
        if not self.is_congested:
            return True
        try:
            await asyncio.wait_for(self._congestion_cleared.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _normalize_weight(self, weight: int) -> int:
        """限制单次请求权重，避免 weight > capacity 导致永久等待"""
        normalized = max(1, min(weight, self._current))
        if normalized != weight:
            logger.warning(
                f"[OCR自适应并发] 请求权重已收敛: weight={weight} -> {normalized}, "
                f"current={self._current}"
            )
        return normalized

    async def acquire(self, weight: int = 1) -> None:
        await self._semaphore.acquire(weight)

    async def release(self, weight: int = 1) -> None:
        await self._semaphore.release(weight)

    @asynccontextmanager
    async def weighted(self, weight: int = 1):
        normalized_weight = self._normalize_weight(weight)
        async with self._semaphore.weighted(normalized_weight):
            yield

    async def record(self, metric: OcrRequestMetric) -> None:
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

            consecutive_fail = self._count_consecutive_tail_failures(metrics)
            if consecutive_fail >= _CONSECUTIVE_FAIL_THRESHOLD:
                new_c = max(self._min_c, math.floor(self._current * 0.5))
                desc = f"consecutive_fail({consecutive_fail}) *0.5 -> {new_c}"
            elif stats["timeout_count"] > 0 or stats["transport_error_count"] > 0:
                new_c = max(self._min_c, math.floor(self._current * 0.7))
                trigger = (
                    "timeout"
                    if stats["timeout_count"] > 0
                    else "transport_error"
                )
                desc = f"{trigger} *0.7 -> {new_c}"
            elif stats["low_confidence_rate"] > 0.35:
                new_c = max(self._min_c, self._current - 1)
                desc = (
                    f"low_confidence({stats['low_confidence_rate']:.0%}) "
                    f"-1 -> {new_c}"
                )
            elif stats["tail_latency_class"]:
                new_c = max(self._min_c, self._current - 1)
                tail = stats["tail_latency_class"]
                desc = (
                    f"tail_latency({tail}: p95={stats['class_p95'][tail]:.0f}>"
                    f"p50*2.5={stats['class_p50'][tail]*2.5:.0f}) "
                    f"-1 -> {new_c}"
                )
            elif stats["p95"] > self._latency_ceiling:
                new_c = max(self._min_c, self._current - 1)
                desc = (
                    f"latency_ceiling(p95={stats['p95']:.0f}>"
                    f"{self._latency_ceiling}) -1 -> {new_c}"
                )
            elif self._should_increase(stats):
                new_c = min(self._max_c, self._current + 1)
                desc = f"stable +1 -> {new_c}"
                self._last_increase_time = time.monotonic()

            if new_c != self._current:
                old_c = self._current
                self._current = new_c
                self._semaphore.resize(new_c)
                self._last_adjust_desc = desc
                if new_c < old_c:
                    self._metrics.clear()
                self._prev_p95 = stats["p95"]
                self._prev_queue_p95 = stats["queue_wait_p95"]
                self._prev_queue_p95_updated_at = time.monotonic()
                logger.info(
                    f"[OCR自适应并发] {desc} "
                    f"(p50={stats['p50']:.0f}ms, p95={stats['p95']:.0f}ms, "
                    f"queue_p95={stats['queue_wait_p95']:.0f}ms, "
                    f"low_conf={stats['low_confidence_rate']:.0%}, "
                    f"err={stats['error_rate']:.0%})"
                )
            else:
                self._prev_p95 = stats["p95"]
                self._prev_queue_p95 = stats["queue_wait_p95"]
                self._prev_queue_p95_updated_at = time.monotonic()

            # 拥堵状态变更 -> 驱动 Event 信号
            now_congested = self.is_congested
            if self._was_congested and not now_congested:
                self._congestion_cleared.set()
                logger.info("[OCR自适应并发] 拥堵已解除, 通知等待中的竞争路由")
            elif now_congested and not self._was_congested:
                self._congestion_cleared.clear()
            self._was_congested = now_congested

    def _should_increase(self, stats: dict) -> bool:
        if self._current >= self._max_c:
            return False
        if stats["timeout_count"] > 0:
            return False
        if stats["transport_error_count"] > 0:
            return False
        if stats["error_rate"] > 0:
            return False
        if stats["low_confidence_rate"] >= 0.15:
            return False
        if stats["p95"] > self._latency_ceiling:
            return False
        if (
            self._prev_p95 is not None
            and self._prev_p95 > 0
            and stats["p95"] > self._prev_p95 * 1.3
        ):
            return False
        queue_growth_allowed = self._current < self._instance_count * 2
        if not queue_growth_allowed:
            if stats["queue_wait_p95"] > self._queue_wait_ceiling:
                return False
            if (
                self._prev_queue_p95 is not None
                and self._prev_queue_p95 > 0
                and stats["queue_wait_p95"] > self._prev_queue_p95 * 1.2
            ):
                return False
        elapsed = time.monotonic() - self._last_increase_time
        if elapsed < self._increase_cooldown:
            return False
        return True

    @staticmethod
    def _compute_window_stats(metrics: list[OcrRequestMetric]) -> dict:
        total = len(metrics)
        if total == 0:
            return {
                "p50": 0,
                "p95": 0,
                "queue_wait_p95": 0,
                "timeout_count": 0,
                "transport_error_count": 0,
                "low_confidence_rate": 0.0,
                "error_rate": 0.0,
                "tail_latency_class": "",
                "class_p50": {},
                "class_p95": {},
            }

        timeout_count = sum(1 for m in metrics if m.is_timeout)
        transport_error_count = sum(1 for m in metrics if m.is_transport_error)
        low_confidence_count = sum(1 for m in metrics if m.is_low_confidence)
        fail_count = sum(1 for m in metrics if not m.success)

        success_latencies = sorted(
            m.latency_ms for m in metrics if m.success
        )
        queue_waits = sorted(m.queue_wait_ms for m in metrics)

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

        class_p50: dict[str, float] = {}
        class_p95: dict[str, float] = {}
        tail_latency_class = ""
        for request_class in sorted({m.request_class for m in metrics}):
            class_latencies = sorted(
                m.latency_ms
                for m in metrics
                if m.success and m.request_class == request_class
            )
            if len(class_latencies) < 5:
                continue
            cls_p50 = class_latencies[len(class_latencies) // 2]
            cls_p95_idx = min(
                int(len(class_latencies) * 0.95),
                len(class_latencies) - 1,
            )
            cls_p95 = class_latencies[cls_p95_idx]
            class_p50[request_class] = cls_p50
            class_p95[request_class] = cls_p95
            if cls_p50 > 0 and cls_p95 > cls_p50 * 2.5:
                tail_latency_class = request_class

        queue_p95_idx = min(int(len(queue_waits) * 0.95), len(queue_waits) - 1)
        queue_wait_p95 = queue_waits[queue_p95_idx] if queue_waits else 0

        return {
            "p50": p50,
            "p95": p95,
            "queue_wait_p95": queue_wait_p95,
            "timeout_count": timeout_count,
            "transport_error_count": transport_error_count,
            "low_confidence_rate": low_confidence_count / total,
            "error_rate": fail_count / total,
            "tail_latency_class": tail_latency_class,
            "class_p50": class_p50,
            "class_p95": class_p95,
        }

    @staticmethod
    def _count_consecutive_tail_failures(metrics: list[OcrRequestMetric]) -> int:
        count = 0
        for m in reversed(metrics):
            if m.is_timeout or m.is_transport_error:
                count += 1
            else:
                break
        return count

    def get_stats(self) -> dict:
        metrics = list(self._metrics)
        stats = self._compute_window_stats(metrics)
        return {
            "concurrency": self._current,
            "p95_ms": int(stats["p95"]),
            "p50_ms": int(stats["p50"]),
            "queue_wait_p95_ms": int(stats["queue_wait_p95"]),
            "low_confidence_rate": round(stats["low_confidence_rate"], 2),
            "error_rate": round(stats["error_rate"], 2),
            "last_adjust": self._last_adjust_desc,
            "window_fill": len(self._metrics),
            "semaphore_used": self._semaphore.used,
            "is_congested": self.is_congested,
        }
