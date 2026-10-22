"""CPU 路径并发路数 AIMD 控制器。

针对 CPU 架构的自适应策略, 与 GPU 的 ``EmbeddingBatchSizeController`` 完全解耦:

| 维度 | GPU 控制器 | CPU 控制器 (本模块) |
|---|---|---|
| 调控对象 | batch_size | 并发路数 (semaphore capacity) |
| 资源约束 | 显存 (硬上限, OOM 即崩) | 核数 (软上限, 争抢即变慢) |
| 失败处理 | OOM/stall 砍批 + 设备熔断 | 仅延迟劣化反馈, 不熔断 |
| 反馈信号 | 单批耗时 + 显存余量 + cudaStream | 单批 latency + queue_wait + OS load |
| 升降档幅度 | 砍半 / +2 | -1 / +1 (与 OcrAdaptiveConcurrency 同步) |

设计参考 ``backend/app/core/parser/adaptive_concurrency.py`` 的
``OcrAdaptiveConcurrency`` 升降档结构, 重用了相同的滚动窗口、stable_samples、
冷却时间语义, 但只调并发路数, 不调批大小。

容量调整通过 ``encode_gate._resize_cpu_encode_semaphore`` 双缓冲替换实现 ——
asyncio.Semaphore 不支持运行时改 capacity, 必须新建并替换全局引用,
旧请求自然 drain。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from loguru import logger


@dataclass(frozen=True)
class _SampleStats:
    """滚动窗口统计快照。"""

    count: int
    p50_latency_ms: float
    p95_latency_ms: float
    queue_wait_p95_ms: float
    success_rate: float
    success_streak: int


class CpuEncodeConcurrencyController:
    """CPU encode 并发路数 AIMD 控制器。

    线程安全: 所有外部入口通过 ``self._lock`` 保护; ``record_metric`` 由
    encode_gate 在工作线程/事件循环里调用, 状态变化只发生在 record 路径,
    应用 (resize semaphore) 也在 record 时同步进行, 避免另起 timer 增加复杂度。
    """

    def __init__(
        self,
        *,
        initial: int,
        min_concurrency: int,
        max_concurrency: int,
        window_size: int = 10,
        increase_cooldown_sec: int = 10,
        latency_ceiling_ms: int = 30000,
        queue_wait_ceiling_ms: int = 5000,
        stable_samples: int = 3,
        load_pressure_factor: float = 1.5,
        effective_cores: int = 1,
    ) -> None:
        self._lock = threading.Lock()

        # clamp 入参, 保证 min<=initial<=max
        self._min = max(1, int(min_concurrency))
        self._max = max(self._min, int(max_concurrency))
        self._initial = max(self._min, min(self._max, int(initial)))
        self._current = self._initial

        self._window_size = max(2, int(window_size))
        self._increase_cooldown_sec = max(1, int(increase_cooldown_sec))
        self._latency_ceiling_ms = max(100, int(latency_ceiling_ms))
        self._queue_wait_ceiling_ms = max(50, int(queue_wait_ceiling_ms))
        self._stable_samples = max(1, int(stable_samples))
        self._load_pressure_factor = max(1.0, float(load_pressure_factor))
        self._effective_cores = max(1, int(effective_cores))

        self._latency_window: deque[float] = deque(maxlen=self._window_size)
        self._queue_wait_window: deque[float] = deque(maxlen=self._window_size)
        self._success_window: deque[bool] = deque(maxlen=self._window_size)

        self._success_streak: int = 0
        self._last_increase_ts: float = 0.0
        self._adjust_total: int = 0

        # 基线 latency: 第一次 stable_samples 内成功 batch 的中位数
        self._baseline_latency_ms: Optional[float] = None

        logger.info(
            f"CpuEncodeConcurrencyController 初始化: "
            f"initial={self._current}, min={self._min}, max={self._max}, "
            f"window={self._window_size}, latency_ceiling={self._latency_ceiling_ms}ms, "
            f"queue_wait_ceiling={self._queue_wait_ceiling_ms}ms, "
            f"effective_cores={self._effective_cores}, "
            f"load_factor={self._load_pressure_factor}"
        )

    # ── 度量入口 ───────────────────────────────────────────────

    def record_metric(
        self,
        *,
        latency_ms: float,
        queue_wait_ms: float,
        success: bool,
    ) -> None:
        """encode_gate 在每批 CPU encode 完成时调用; 触发窗口统计与升降档。"""

        with self._lock:
            self._latency_window.append(float(latency_ms))
            self._queue_wait_window.append(float(queue_wait_ms))
            self._success_window.append(bool(success))
            if success:
                self._success_streak += 1
            else:
                self._success_streak = 0

            if len(self._latency_window) < self._window_size:
                return  # 窗口未满, 不调

            stats = self._compute_stats_locked()

            # 尝试基线锚定 (用窗口稳定后的 p50 作为基线)
            if (
                self._baseline_latency_ms is None
                and stats.success_rate >= 0.8
            ):
                self._baseline_latency_ms = stats.p50_latency_ms
                logger.debug(
                    f"[CpuEncodeConcurrency] 锚定基线 latency: "
                    f"{self._baseline_latency_ms:.0f}ms"
                )

            self._maybe_adjust_locked(stats)

    # ── 统计与判定 ───────────────────────────────────────────

    def _compute_stats_locked(self) -> _SampleStats:
        latencies = sorted(self._latency_window)
        queues = sorted(self._queue_wait_window)
        n = len(latencies)
        idx_p50 = max(0, int(n * 0.5) - 1) if n > 1 else 0
        idx_p95 = min(n - 1, int(n * 0.95))
        success_rate = (
            sum(1 for s in self._success_window if s) / max(1, len(self._success_window))
        )
        return _SampleStats(
            count=n,
            p50_latency_ms=latencies[idx_p50] if latencies else 0.0,
            p95_latency_ms=latencies[idx_p95] if latencies else 0.0,
            queue_wait_p95_ms=queues[idx_p95] if queues else 0.0,
            success_rate=success_rate,
            success_streak=self._success_streak,
        )

    def _read_load_pressure(self) -> Optional[float]:
        """返回归一化系统压力 (load / effective_cores), Windows 用 cpu_percent 估算。

        Returns:
            None: 无法获取 (兼容容器/沙箱), 跳过此判定
            float: >=0; 例如 1.0 表示 load 等于核数; 大于 load_factor 表示需降档
        """

        if sys.platform.startswith("linux"):
            try:
                load_1m = os.getloadavg()[0]
                return load_1m / float(self._effective_cores)
            except Exception:  # noqa: BLE001
                return None

        # Windows / macOS 没有 getloadavg; 用 psutil.cpu_percent 估算压力
        try:
            import psutil

            cpu_pct = psutil.cpu_percent(interval=None)
            # cpu_percent 是 0-100, 100% 视为 load=cores (即压力 1.0)
            return cpu_pct / 100.0
        except Exception:  # noqa: BLE001
            return None

    def _maybe_adjust_locked(self, stats: _SampleStats) -> None:
        # ── 降档判定 ──
        if self._baseline_latency_ms is not None and stats.p95_latency_ms > self._baseline_latency_ms * 2:
            self._decrease_locked(reason="latency_doubled")
            return
        if stats.p95_latency_ms > self._latency_ceiling_ms:
            self._decrease_locked(reason="latency_ceiling")
            return
        if stats.queue_wait_p95_ms > self._queue_wait_ceiling_ms:
            self._decrease_locked(reason="queue_wait_ceiling")
            return
        if stats.success_rate < 0.5:
            self._decrease_locked(reason="success_rate_low")
            return
        load_pressure = self._read_load_pressure()
        if load_pressure is not None and load_pressure > self._load_pressure_factor:
            self._decrease_locked(reason=f"load_pressure({load_pressure:.2f})")
            return

        # ── 升档判定 ──
        if self._current >= self._max:
            return
        if stats.success_streak < self._stable_samples:
            return
        now = time.monotonic()
        if now - self._last_increase_ts < self._increase_cooldown_sec:
            return
        if stats.p95_latency_ms > self._latency_ceiling_ms * 0.7:
            return  # 已接近 ceiling, 不再加压
        if stats.queue_wait_p95_ms > self._queue_wait_ceiling_ms * 0.7:
            return
        if load_pressure is not None and load_pressure > self._load_pressure_factor * 0.85:
            return

        self._increase_locked(reason="stable")

    def _decrease_locked(self, *, reason: str) -> None:
        if self._current <= self._min:
            return
        old = self._current
        self._current = max(self._min, self._current - 1)
        self._success_streak = 0
        self._adjust_total += 1
        self._apply_capacity(self._current)
        self._record_adjust_metric("down", reason)
        logger.info(
            f"[CpuEncodeConcurrency] 降档: {old} -> {self._current} "
            f"(reason={reason}, total={self._adjust_total})"
        )

    def _increase_locked(self, *, reason: str) -> None:
        if self._current >= self._max:
            return
        old = self._current
        self._current = min(self._max, self._current + 1)
        self._success_streak = 0
        self._last_increase_ts = time.monotonic()
        self._adjust_total += 1
        self._apply_capacity(self._current)
        self._record_adjust_metric("up", reason)
        logger.info(
            f"[CpuEncodeConcurrency] 升档: {old} -> {self._current} "
            f"(reason={reason}, total={self._adjust_total})"
        )

    @staticmethod
    def _apply_capacity(new_capacity: int) -> None:
        from app.core.vectorizer.encode_gate import _resize_cpu_encode_semaphore

        try:
            _resize_cpu_encode_semaphore(new_capacity)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[CpuEncodeConcurrency] resize semaphore 失败 (忽略): {exc}"
            )

    @staticmethod
    def _record_adjust_metric(direction: str, reason: str) -> None:
        try:
            from app.core.observability.metrics import (
                vector_cpu_concurrency_adjust_total,
            )

            vector_cpu_concurrency_adjust_total.labels(
                direction=direction, reason=reason
            ).inc()
        except Exception:  # noqa: BLE001
            pass

    # ── 观测接口 ───────────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "current": self._current,
                "min": self._min,
                "max": self._max,
                "window_size": self._window_size,
                "success_streak": self._success_streak,
                "adjust_total": self._adjust_total,
                "baseline_latency_ms": self._baseline_latency_ms,
                "samples_in_window": len(self._latency_window),
            }


_controller: Optional[CpuEncodeConcurrencyController] = None
_controller_lock = threading.Lock()


def _build_controller_from_settings() -> CpuEncodeConcurrencyController:
    from app.core.config import settings
    from app.core.runtime.cpu_budget import get_cpu_budget

    budget = get_cpu_budget()
    min_c = max(1, int(getattr(settings, "VECTOR_CPU_CONCURRENCY_MIN", 1)))
    max_override = getattr(settings, "VECTOR_CPU_CONCURRENCY_MAX", None)
    budget_max = max(1, int(budget.max_concurrency))
    max_c = max(min_c, int(max_override) if max_override else budget_max)
    raw_initial = int(getattr(settings, "VECTOR_CPU_CONCURRENCY_INITIAL", min_c))
    initial = max(min_c, min(max_c, raw_initial))

    if raw_initial != initial:
        logger.info(
            f"CpuEncodeConcurrencyController 初始并发已按配置边界 clamp: "
            f"raw_initial={raw_initial}, resolved_initial={initial}, "
            f"min={min_c}, max={max_c}, budget_max={budget_max}"
        )

    return CpuEncodeConcurrencyController(
        initial=initial,
        min_concurrency=min_c,
        max_concurrency=max_c,
        window_size=int(getattr(settings, "VECTOR_CPU_CONCURRENCY_WINDOW_SIZE", 10)),
        increase_cooldown_sec=int(
            getattr(settings, "VECTOR_CPU_CONCURRENCY_INCREASE_COOLDOWN", 10)
        ),
        latency_ceiling_ms=int(
            getattr(settings, "VECTOR_CPU_CONCURRENCY_LATENCY_CEILING_MS", 30000)
        ),
        queue_wait_ceiling_ms=int(
            getattr(settings, "VECTOR_CPU_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 5000)
        ),
        load_pressure_factor=float(
            getattr(settings, "VECTOR_CPU_LOAD_PRESSURE_FACTOR", 1.5)
        ),
        effective_cores=budget.effective_cores,
    )


def get_cpu_encode_concurrency_controller() -> CpuEncodeConcurrencyController:
    """全进程单例; 首次调用时初始化并注册到 encode_gate.set_cpu_metric_handler。"""

    global _controller
    if _controller is not None:
        return _controller
    with _controller_lock:
        if _controller is not None:
            return _controller
        controller = _build_controller_from_settings()
        _controller = controller

        # 注册度量回调, encode_gate 后续每批 CPU encode 完成都会调用
        from app.core.vectorizer.encode_gate import (
            CpuEncodeMetric,
            _resize_cpu_encode_semaphore,
            set_cpu_metric_handler,
        )

        # 立即把 semaphore 容量同步到控制器初始值
        _resize_cpu_encode_semaphore(controller._current)

        def _handle_metric(metric: CpuEncodeMetric) -> None:
            controller.record_metric(
                latency_ms=metric.latency_ms,
                queue_wait_ms=metric.queue_wait_ms,
                success=metric.success,
            )

        set_cpu_metric_handler(_handle_metric)
        logger.info(
            f"CpuEncodeConcurrencyController 已挂载到 encode_gate: "
            f"capacity={controller._current}"
        )
        return controller


def reset_cpu_encode_concurrency_controller() -> None:
    """清除单例缓存 + 卸载 handler, 仅供单元测试使用。"""

    global _controller
    with _controller_lock:
        _controller = None
    try:
        from app.core.vectorizer.encode_gate import set_cpu_metric_handler

        set_cpu_metric_handler(None)
    except Exception:  # noqa: BLE001
        pass
