"""全进程唯一的 embedding encode 串行闸门 + OOM/卡死降级 helper。

所有向量化路径（VectorIndexer / MaterialIndexer / WritingService）
在调用 EmbeddingModel.encode 前必须先获取此锁，避免多路径同时
占用同一张 GPU 导致显存峰值叠加 OOM。

``safe_encode`` 是旧调用方兼容入口，内部复用 ``safe_encode_batch``
的超时、降批、CPU 兜底与设备冷却合同。

``safe_encode_batch`` 面向文档主链路的单批安全 encode，
两层防御：GPU 线程超时 -> 主进程 CPU 回退（复用缓存模型）。

第三轮改进：
- 去掉子进程 GPU 缩批重试链（GPU stall 时缩批无法解决显存竞争）
- CPU 回退在主进程线程中执行，复用缓存的 CPU 模型实例，避免
 反复 spawn 子进程 + 重新加载模型带来的雪崩
- 首次超时阈值对历史数据不足时更宽松（45s），减少大 batch 误判
- 保留子进程 encode 能力作为最终 fallback（主进程 CPU 也失败时）

第四轮改进（GPU 100% 时假回退冻结修复）：
- _try_clear_cuda_cache 改为 async + 5s 自身超时，防止 cudaStreamSynchronize
 在 GPU 满载时同步阻塞 asyncio 事件循环（症状：stall warning 后无后续日志）
- GPU 首轮走专属 ThreadPoolExecutor(max_workers=1)，超时后把未完成的 future
 stash 为"孤儿 future"；下一批开始前若孤儿仍在跑，直接走 CPU 回退避免雪崩
- 超时基线放宽：下限 20->45s，倍率 3->5，上限 60->120，
 配合事件循环解冻，避免 GPU 共享场景下的频繁误判 stall

第六轮改进（AIMD 批大小动态调节，取代原来的"预防性 CPU 回退"方案）：
- 新增 EmbeddingBatchSizeController：类比 OcrAdaptiveConcurrency，
 但调的是 batch_size 而不是 concurrency
 * 初始 bs=12（小批起步，避免一上来就撞 GPU 满载）
 * 连续 STABLE_SAMPLES=3 批成功且 elapsed 稳定 → bs += 2（加法慢升）
 * 任何 stall/OOM → bs //= 2（乘法快降）并在同批用新 bs 立即重试
 * clamp 在 [MIN=4, MAX=32]
- safe_encode_batch 主路径改为"降批重试优先 + CPU 只作兜底"：
 * GPU 首轮失败 → 控制器 record_degrade 自动降批 → 在同批重试（最多 3 次）
 * bs 已降到 MIN 仍失败才进入 CPU 兜底（罕见，保留主进程 + 子进程两层）
 * 删掉了第四/五轮引入的 OCR 压力探针（信号错配）和 EmbeddingDeviceController
 （AIMD 冷却只做设备二元跳变，不解决吞吐问题）

第八轮改进（取消 GPU 单次重试 + 任务级 CPU 粘滞下沉）：
- 取消 GPU 段的 3 次重试链: 单次 stall/OOM 即触发 device_cooldown
 并直接进入 CPU 兜底; 实测重试无法缓解 GPU 拥堵, 反而通过 orphan future 雪崩
- 任务级粘滞 CPU 通过 ContextVar 下沉到 worker job 边界: 一次 hard
 fallback 后, 同一 ARQ job 内剩余 encode 全部跳 GPU, 消除"BID 提取等检索路径
 反复 GPU stall→CPU 回退"的拉锯

第七轮改进（设备级冷却/熔断，补齐设备健康语义）：
- 新增 GpuDeviceCooldownController：在 AIMD 之上再加一层设备级熔断。
 * 触发：bs 已达 MIN 仍 stall/OOM、或单次批因 orphan_running 多次跳 GPU
 * 行为：进入冷却窗口 (默认 60s, 可通过 settings.VECTOR_GPU_COOLDOWN_SEC 调)
 冷却期内新批次直接走 CPU 兜底，不再试 GPU，消除"CPU 刚回退又回 GPU 再 stall"抖动
 * 退出：冷却到期后让下一批做 probe, probe 成功则 reset, 失败则指数退避延长冷却
- safe_encode_batch 新增 stick_to_cpu 形参：上层（文档/素材批任务）一旦在某一批
 发生降级，可让同一任务后续批次强制走 CPU，避免任务内部抖动
"""

import asyncio
import concurrent.futures
import contextlib
import contextvars
import multiprocessing
import threading
import time
from dataclasses import dataclass
from typing import Iterator, Optional, Callable

import numpy as np
from loguru import logger

from app.core.config import settings
from app.core.observability.metrics import (
    vector_cpu_concurrency_gauge,
    vector_cpu_inflight_gauge,
    vector_cpu_queue_wait_seconds,
    vector_encode_batch_chars,
    vector_encode_batch_total,
    vector_encode_latency_seconds,
    vector_fallback_total,
)
from app.core.vectorizer.offload_clock import (
    OffloadObservation,
    observed_call,
    wait_for_offload,
)

_encode_lock: Optional[asyncio.Lock] = None

# ── CPU 路径独立锁: 与 GPU 的 encode_lock 完全解耦 ───────────────────────
# GPU 必须串行 (单设备), CPU 必须并发 (多核); 用 Semaphore 控制并发路数。
# 容量由 cpu_budget.max_concurrency 决定, 第四层 AIMD 控制器可通过
# _resize_cpu_encode_semaphore 双缓冲替换 (asyncio.Semaphore 不支持运行时改容量)。
_cpu_encode_semaphore: Optional[asyncio.Semaphore] = None
_cpu_encode_capacity: int = 0
_cpu_encode_semaphore_lock = threading.Lock()


@dataclass(frozen=True)
class CpuEncodeMetric:
    """CPU 路径单批 encode 的反馈度量, 由 AIMD 控制器消费。"""

    latency_ms: float
    queue_wait_ms: float
    batch_size: int
    total_chars: int
    success: bool


# AIMD 控制器在 cpu_aimd_controller 模块中注入此 handler;
# encode_gate 只负责采集 + 投递, 不感知具体策略。
_cpu_metric_handler: Optional[Callable[[CpuEncodeMetric], None]] = None
_cpu_metric_handler_lock = threading.Lock()


def set_cpu_metric_handler(
    handler: Optional[Callable[[CpuEncodeMetric], None]]
) -> None:
    """注册/卸载 CPU encode 度量回调; None 表示卸载。"""

    global _cpu_metric_handler
    with _cpu_metric_handler_lock:
        _cpu_metric_handler = handler


def _record_cpu_metric(metric: CpuEncodeMetric) -> None:
    """将单批 CPU encode 度量投递给已注册的 handler; 异常一律 swallow。"""

    handler = _cpu_metric_handler
    if handler is None:
        return
    try:
        handler(metric)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[cpu_metric_handler] 度量投递失败 (忽略): {exc}")


def _resolve_cpu_encode_capacity() -> int:
    """从 cpu_budget 解析 CPU 路径并发上限; budget 不可用时退化为 1。"""

    try:
        from app.core.runtime.cpu_budget import get_cpu_budget

        return max(1, int(get_cpu_budget().max_concurrency))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[cpu_budget] 解析失败, 退化 capacity=1: {exc}")
        return 1


def _get_cpu_encode_semaphore() -> asyncio.Semaphore:
    """懒初始化 CPU encode semaphore; 容量取自 cpu_budget。"""

    global _cpu_encode_semaphore, _cpu_encode_capacity
    if _cpu_encode_semaphore is not None:
        return _cpu_encode_semaphore
    with _cpu_encode_semaphore_lock:
        if _cpu_encode_semaphore is None:
            capacity = _resolve_cpu_encode_capacity()
            _cpu_encode_semaphore = asyncio.Semaphore(capacity)
            _cpu_encode_capacity = capacity
            logger.info(
                f"[cpu_encode_semaphore] 初始化, capacity={capacity}"
            )
    return _cpu_encode_semaphore


def get_cpu_encode_capacity() -> int:
    """对外暴露当前 CPU semaphore 容量, 供观测/AIMD 决策使用。"""

    if _cpu_encode_semaphore is None:
        return _resolve_cpu_encode_capacity()
    return _cpu_encode_capacity


def _resize_cpu_encode_semaphore(new_capacity: int) -> bool:
    """双缓冲替换 CPU semaphore; 旧请求自然 drain, 新请求看到新容量。

    asyncio.Semaphore 不支持运行时改 capacity, 因此 AIMD 调整时必须
    新建一个 Semaphore 并替换全局引用; 已经 acquire 的旧请求继续走旧 sem,
    新发起的 acquire 会拿到新 sem。

    Returns:
        True 表示发生了替换; False 表示容量未变 (no-op)。
    """

    global _cpu_encode_semaphore, _cpu_encode_capacity
    new_capacity = max(1, int(new_capacity))
    with _cpu_encode_semaphore_lock:
        if new_capacity == _cpu_encode_capacity and _cpu_encode_semaphore is not None:
            return False
        _cpu_encode_semaphore = asyncio.Semaphore(new_capacity)
        old_capacity = _cpu_encode_capacity
        _cpu_encode_capacity = new_capacity
    try:
        vector_cpu_concurrency_gauge.set(new_capacity)
    except Exception:  # noqa: BLE001
        pass
    logger.info(
        f"[cpu_encode_semaphore] 容量调整: {old_capacity} -> {new_capacity}"
    )
    return True


def _reset_cpu_encode_semaphore() -> None:
    """清除全局 CPU semaphore 状态, 仅供单元测试使用。"""

    global _cpu_encode_semaphore, _cpu_encode_capacity
    with _cpu_encode_semaphore_lock:
        _cpu_encode_semaphore = None
        _cpu_encode_capacity = 0

# GPU 与 OCR/VL 共享物理卡时，encode 耗时抖动较大，
# 下限从 20s 放宽到 45s、倍率 3->5、上限 60->120，减少误判。
# 前提：S1 已把 _try_clear_cuda_cache 改为 async 版本，
# 超时期间事件循环不会被冻结，因此放宽超时是安全的。
BATCH_STALL_TIMEOUT_SEC: float = 120.0
BATCH_STALL_TIMEOUT_MIN_SEC: float = 45.0
BATCH_STALL_TIMEOUT_COLD_SEC: float = 45.0
BATCH_STALL_TIMEOUT_MULTIPLIER: float = 5.0

CPU_FALLBACK_TIMEOUT_SEC: float = 120.0

_rolling_batch_durations: list[float] = []
_ROLLING_WINDOW_SIZE = 20

# ── 子进程 encode 池（仅用于最终 fallback） ──────────────────────────────
_encode_executor: Optional[concurrent.futures.ProcessPoolExecutor] = None
_encode_executor_lock = threading.Lock()

# ── CPU 回退模型缓存（进程级单例） ───────────────────────────────────────
_cpu_model_cache = None
_cpu_model_cache_lock = threading.Lock()

# ── GPU encode 专属线程池 + 孤儿 future 看护 ────────────────────────────
# 专用单 worker 线程池，便于记录"在飞 future"引用以判定前一批 encode
# 是否仍在抢占 GPU（asyncio.wait_for 超时无法真正 cancel 线程）。
_gpu_encode_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
_gpu_encode_executor_lock = threading.Lock()
# loop.run_in_executor 返回 asyncio.Future（内部 wrap 了线程池的
# concurrent.futures.Future），其 done()/add_done_callback 会在
# 线程实际完成时被触发，所以用它来判定孤儿状态是可靠的。
_last_orphan_future: Optional["asyncio.Future"] = None
_orphan_lock = threading.Lock()


def _get_gpu_encode_executor() -> concurrent.futures.ThreadPoolExecutor:
    """懒初始化 GPU encode 专属线程池（max_workers=1，串行化 GPU 使用）。"""
    global _gpu_encode_executor
    with _gpu_encode_executor_lock:
        if _gpu_encode_executor is None:
            _gpu_encode_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="gpu-encode",
            )
    return _gpu_encode_executor


def _clear_orphan_if(future: "asyncio.Future") -> None:
    """future 完成时清理 _last_orphan_future 引用（若仍指向它）。"""
    global _last_orphan_future
    with _orphan_lock:
        if _last_orphan_future is future:
            _last_orphan_future = None


def _stash_orphan(future: "asyncio.Future") -> None:
    """记录当前超时未完成的 future 作为"孤儿"，并挂清理回调。"""
    global _last_orphan_future
    with _orphan_lock:
        _last_orphan_future = future
    future.add_done_callback(_clear_orphan_if)


def _orphan_still_running() -> bool:
    """检查是否有上一批超时的 encode 仍在跑（仍在占 GPU）。"""
    with _orphan_lock:
        fut = _last_orphan_future
    return fut is not None and not fut.done()


def _get_encode_executor() -> concurrent.futures.ProcessPoolExecutor:
    """懒初始化 encode 子进程池，与 parse_worker 模式一致。"""
    global _encode_executor
    with _encode_executor_lock:
        if _encode_executor is not None and getattr(_encode_executor, "_broken", False):
            logger.warning("[encode进程池] 检测到 BrokenProcessPool，正在重建")
            try:
                _encode_executor.shutdown(wait=False)
            except Exception:
                pass
            _encode_executor = None
        if _encode_executor is None:
            _encode_executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
            )
    return _encode_executor


def _reset_encode_executor() -> None:
    """强制重置 encode 子进程池。"""
    global _encode_executor
    with _encode_executor_lock:
        if _encode_executor is not None:
            try:
                _encode_executor.shutdown(wait=False)
            except Exception:
                pass
            _encode_executor = None
    logger.info("[encode进程池] 已重置，下次调用将重建")


def get_encode_lock() -> asyncio.Lock:
    """返回全局唯一的 asyncio.Lock 实例。"""
    global _encode_lock
    if _encode_lock is None:
        _encode_lock = asyncio.Lock()
    return _encode_lock


def _is_cuda_oom(exc: BaseException) -> bool:
    """判断异常是否为 CUDA OOM。"""
    try:
        import torch  # noqa: F401
        if isinstance(exc, torch.cuda.OutOfMemoryError):
            return True
    except (ImportError, AttributeError):
        pass
    return isinstance(exc, RuntimeError) and "CUDA out of memory" in str(exc)


def _try_clear_cuda_cache() -> None:
    """尽力释放 PyTorch 缓存块（不影响常驻权重）。"""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


async def _try_clear_cuda_cache_async(timeout: float = 5.0) -> None:
    """异步安全版本：将 empty_cache 放入线程并带自身超时，避免阻塞事件循环。

    GPU 100% 满载时 torch.cuda.empty_cache 可能触发 cudaStreamSynchronize
    从而长时间阻塞。此处走 to_thread + wait_for，让事件循环在 GPU 仍被
    占用时也能继续推进到 CPU 回退分支。超时/异常一律 swallow，不向上抛出。
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_try_clear_cuda_cache),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.debug(
            f"[_try_clear_cuda_cache_async] empty_cache 超时 {timeout:.1f}s, 跳过"
        )
    except Exception as e:
        logger.debug(f"[_try_clear_cuda_cache_async] 异常忽略: {e}")


def _record_batch_duration(elapsed_sec: float) -> None:
    """记录一次成功 batch 的耗时用于滚动 p95 估算。"""
    _rolling_batch_durations.append(elapsed_sec)
    if len(_rolling_batch_durations) > _ROLLING_WINDOW_SIZE:
        _rolling_batch_durations.pop(0)


def _get_adaptive_stall_timeout(
    batch_size: int = 32,
    texts: Optional[list[str]] = None,
) -> float:
    """基于历史 batch 耗时、batch_size 和当前文本压力计算分级超时。

 冷启动（历史 < 3）使用 BATCH_STALL_TIMEOUT_COLD_SEC (45s)，
 避免大文本 batch 在首次运行时被误判为 stall。: 尾批 batch_size 可能较小, 但单条文本显著变长。此时仅按
 batch_size 缩短 timeout 会把可预期长耗时误判为 GPU stall。
 """
    ratio = max(batch_size, 1) / 32.0
    ratio = min(ratio, 1.0)

    if len(_rolling_batch_durations) < 3:
        base = BATCH_STALL_TIMEOUT_COLD_SEC
    else:
        sorted_d = sorted(_rolling_batch_durations)
        idx = min(int(len(sorted_d) * 0.95), len(sorted_d) - 1)
        p95 = sorted_d[idx]
        base = max(BATCH_STALL_TIMEOUT_MIN_SEC, min(p95 * BATCH_STALL_TIMEOUT_MULTIPLIER, BATCH_STALL_TIMEOUT_SEC))

    scaled = base * max(ratio, 0.4)
    if texts:
        max_chars = max(len(text or "") for text in texts)
        long_text_threshold = max(
            1,
            int(getattr(settings, "VECTOR_MAX_ITEM_CHARS", 1800)),
        )
        if max_chars > long_text_threshold:
            total_chars = sum(len(text or "") for text in texts)
            long_text_floor = (
                BATCH_STALL_TIMEOUT_MIN_SEC
                + (max_chars / 1000.0) * 20.0
                + (total_chars / 1000.0) * 1.0
            )
            scaled = max(scaled, long_text_floor)
    return max(
        BATCH_STALL_TIMEOUT_MIN_SEC,
        min(BATCH_STALL_TIMEOUT_SEC, scaled),
    )


def estimate_cpu_fallback_timeout(texts: list[str]) -> float:
    """按文本总量估算 CPU fallback 超时。"""
    if not texts:
        return CPU_FALLBACK_TIMEOUT_SEC
    total_chars = sum(len(text or "") for text in texts)
    max_chars = max(len(text or "") for text in texts)
    base = float(getattr(settings, "VECTOR_CPU_FALLBACK_BASE_SEC", 45.0))
    per_1k = float(getattr(settings, "VECTOR_CPU_FALLBACK_PER_1K_CHARS_SEC", 18.0))
    max_timeout = float(getattr(settings, "VECTOR_CPU_FALLBACK_MAX_SEC", 360.0))
    estimated = base + (total_chars / 1000.0) * per_1k + (max_chars / 1000.0) * 4.0
    return max(CPU_FALLBACK_TIMEOUT_SEC, min(max_timeout, estimated))


class EncodeStallError(Exception):
    """单批 encode 超时卡死。"""


class EmbeddingBatchSizeController:
    """AIMD 批大小控制器（类比 OcrAdaptiveConcurrency，但调的是 batch_size）。

    设计要点：
    - 小批起步（INITIAL=12），避免一上来就撞 OCR/VL 满载的 GPU
    - 加法慢升：连续 STABLE_SAMPLES=3 次成功 + 距上次 +step 超过 cooldown
      → current_bs += INCREASE_STEP（clamp 到 MAX）
    - 乘法快降：任何 stall/OOM → current_bs = max(MIN, current_bs // 2)
    - 线程安全，使用 time.monotonic 避免系统时间跳变干扰

    第八轮改进（耗时感知软降级，可选开关，默认关闭）：
    - 在原有"超时/不超时"二元判断之外，增加 elapsed_ms 与历史 p95 比值的
      软降级分支，解决"非超时型慢化"对 AIMD 的盲区
    - PAUSE_RATIO（默认 2.0）：elapsed > p95×ratio → 仅清零 streak（不升不降）
    - TRIGGER_RATIO（默认 3.0）：elapsed > p95×ratio → 主动降一档（clamp MIN）
    - 冷启动样本不足（< MIN_SAMPLES=20）→ 完全跳过软降级，走原逻辑
    - 软降级有自身冷却（COOLDOWN_SEC=30），防止与升档周期共振
    - 默认 ENABLED=False，灰度可控；最坏情况一键关闭回退到现状

    与错误的"EmbeddingDeviceController"区别：
    - 旧方案调的是 GPU/CPU 二元选择，stall 后长冷却走 CPU
    - 本方案调的是 GPU 上的 batch_size，永不主动弃 GPU
    - CPU 兜底只在 bs 已降到 MIN 仍失败时才触发（罕见）
    """

    _INITIAL: int = 12
    _MIN: int = 4
    _DEFAULT_MAX: int = 32
    _DEFAULT_INCREASE_STEP: int = 2
    _HARD_MAX: int = 256  # 物理上限保护，防止 settings 误填巨大值导致 OOM
    _INCREASE_COOLDOWN_SEC: float = 15.0
    _STABLE_SAMPLES: int = 3
    _ELAPSED_WINDOW_DEFAULT: int = 60

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # _MAX 与 _INCREASE_STEP 从 settings 读取，clamp 到合法区间；
        # 默认值保持与硬编码版本一致（32 / 2），仅 docker-compose env 显式
        # 覆盖时（如 A10 24GB 卡设 MAX=64/STEP=4）才会变化，本地行为不受影响
        self._MAX: int = self._resolve_max_bs()
        self._INCREASE_STEP: int = self._resolve_increase_step(self._MAX)
        self._current_bs: int = min(self._INITIAL, self._MAX)
        self._success_streak: int = 0
        self._last_increase_ts: float = 0.0
        self._last_degrade_reason: Optional[str] = None
        self._degrade_total: int = 0
        self._increase_total: int = 0
        self._elapsed_window: list[float] = []
        self._last_soft_degrade_ts: float = 0.0
        self._soft_degrade_total: int = 0
        self._pause_total: int = 0
        self._increase_pause_total: int = 0
        self._last_increase_pause_reason: Optional[str] = None

    @classmethod
    def _resolve_max_bs(cls) -> int:
        """从 settings 读 MAX 并 clamp 到 [_MIN, _HARD_MAX]，越界写日志退默认。"""
        try:
            raw = int(getattr(settings, "VECTOR_GPU_AIMD_MAX_BS", cls._DEFAULT_MAX))
        except Exception:
            raw = cls._DEFAULT_MAX
        if raw < cls._MIN:
            logger.warning(
                f"[EmbeddingBatchSizeController] VECTOR_GPU_AIMD_MAX_BS={raw} "
                f"< _MIN={cls._MIN}, clamp 为 {cls._MIN}"
            )
            return cls._MIN
        if raw > cls._HARD_MAX:
            logger.warning(
                f"[EmbeddingBatchSizeController] VECTOR_GPU_AIMD_MAX_BS={raw} "
                f"> _HARD_MAX={cls._HARD_MAX}, clamp 为 {cls._HARD_MAX}"
            )
            return cls._HARD_MAX
        return raw

    @classmethod
    def _resolve_increase_step(cls, max_bs: int) -> int:
        """读 INCREASE_STEP 并 clamp 到 [1, max_bs]，越界写日志退默认。"""
        try:
            raw = int(getattr(
                settings, "VECTOR_GPU_AIMD_INCREASE_STEP", cls._DEFAULT_INCREASE_STEP
            ))
        except Exception:
            raw = cls._DEFAULT_INCREASE_STEP
        if raw < 1:
            logger.warning(
                f"[EmbeddingBatchSizeController] VECTOR_GPU_AIMD_INCREASE_STEP={raw} "
                f"< 1, clamp 为 1"
            )
            return 1
        if raw > max_bs:
            logger.warning(
                f"[EmbeddingBatchSizeController] VECTOR_GPU_AIMD_INCREASE_STEP={raw} "
                f"> _MAX={max_bs}, clamp 为 _MAX"
            )
            return max_bs
        return raw

    @property
    def current_bs(self) -> int:
        with self._lock:
            return self._current_bs

    @property
    def min_bs(self) -> int:
        return self._MIN

    def _read_soft_degrade_settings(self) -> dict:
        """运行时读取软降级配置；任何失败 → 返回禁用语义，保证不破坏现状。"""
        try:
            return {
                "enabled": bool(getattr(
                    settings, "VECTOR_GPU_AIMD_SOFT_DEGRADE_ENABLED", False
                )),
                "min_samples": int(getattr(
                    settings, "VECTOR_GPU_AIMD_SOFT_DEGRADE_MIN_SAMPLES", 20
                )),
                "pause_ratio": float(getattr(
                    settings, "VECTOR_GPU_AIMD_SOFT_DEGRADE_PAUSE_RATIO", 2.0
                )),
                "trigger_ratio": float(getattr(
                    settings, "VECTOR_GPU_AIMD_SOFT_DEGRADE_TRIGGER_RATIO", 3.0
                )),
                "cooldown_sec": float(getattr(
                    settings, "VECTOR_GPU_AIMD_SOFT_DEGRADE_COOLDOWN_SEC", 30.0
                )),
                "step": int(getattr(
                    settings, "VECTOR_GPU_AIMD_SOFT_DEGRADE_STEP", 2
                )),
                "window_size": int(getattr(
                    settings,
                    "VECTOR_GPU_AIMD_ELAPSED_WINDOW_SIZE",
                    self._ELAPSED_WINDOW_DEFAULT,
                )),
            }
        except Exception:
            return {
                "enabled": False, "min_samples": 20, "pause_ratio": 2.0,
                "trigger_ratio": 3.0, "cooldown_sec": 30.0, "step": 2,
                "window_size": self._ELAPSED_WINDOW_DEFAULT,
            }

    def _push_elapsed_unlocked(self, elapsed_ms: float, window_size: int) -> None:
        """把本次 elapsed 推入滚动窗口；必须在持锁内调用。"""
        self._elapsed_window.append(float(elapsed_ms))
        if window_size <= 0:
            window_size = self._ELAPSED_WINDOW_DEFAULT
        while len(self._elapsed_window) > window_size:
            self._elapsed_window.pop(0)

    def _compute_p95_unlocked(self) -> Optional[float]:
        """基于当前滚动窗口计算 p95；窗口为空返回 None。必须在持锁内调用。"""
        if not self._elapsed_window:
            return None
        sorted_window = sorted(self._elapsed_window)
        idx = min(int(len(sorted_window) * 0.95), len(sorted_window) - 1)
        return sorted_window[idx]

    def record_success(
        self,
        elapsed_ms: int,
        increase_allowed: bool = True,
        increase_pause_reason: Optional[str] = None,
    ) -> None:
        """GPU encode 成功回馈。连续稳定 N 次后加法慢升。

        第八轮新增：在 enable 时增加耗时感知分支
        - elapsed_ms > p95 × TRIGGER_RATIO → 主动降一档（带冷却保护）
        - elapsed_ms > p95 × PAUSE_RATIO   → 仅清零 success_streak（不升不降）
        未启用 / 冷启动样本不足 → 完全走原"加法慢升"逻辑，与现状一致

        水位门控：当 GPU 显存水位预测不允许继续升档时，只暂停本次升档，
        不降批、不触发 CPU fallback，避免把容量保护误解释为 GPU 故障。

        关键实现细节：必须先基于"历史窗口（不含当前样本）"算 p95，
        再做软降级判断，最后再把当前样本 push 进窗口。
        否则 p95 永远包含当前样本，比值恒 ≤ 1，软降级永不触发。
        """
        cfg = self._read_soft_degrade_settings()
        with self._lock:
            soft_action = "none"
            p95: Optional[float] = None
            ratio = 0.0
            if cfg["enabled"] and len(self._elapsed_window) >= cfg["min_samples"]:
                p95 = self._compute_p95_unlocked()
                if p95 is not None and p95 > 0:
                    ratio = elapsed_ms / p95
                    now = time.monotonic()
                    in_cooldown = (
                        now - self._last_soft_degrade_ts < cfg["cooldown_sec"]
                    )
                    if elapsed_ms > p95 * cfg["trigger_ratio"]:
                        soft_action = "trigger" if not in_cooldown else "pause"
                    elif elapsed_ms > p95 * cfg["pause_ratio"]:
                        soft_action = "pause"

            self._push_elapsed_unlocked(elapsed_ms, cfg["window_size"])

            if soft_action == "trigger":
                old_bs = self._current_bs
                self._current_bs = max(
                    self._MIN, self._current_bs - cfg["step"]
                )
                self._success_streak = 0
                self._last_soft_degrade_ts = time.monotonic()
                self._soft_degrade_total += 1
                if old_bs != self._current_bs:
                    logger.warning(
                        f"[EmbeddingBatchSizeController] 软降级 bs "
                        f"{old_bs} -> {self._current_bs} "
                        f"(elapsed_ms={elapsed_ms}, p95_ms={p95:.0f}, "
                        f"ratio={ratio:.1f}x, "
                        f"累计软降级={self._soft_degrade_total}次)"
                    )
                else:
                    logger.warning(
                        f"[EmbeddingBatchSizeController] 软降级触发但 bs "
                        f"已达下限 {self._MIN} 无法再降 "
                        f"(elapsed_ms={elapsed_ms}, p95_ms={p95:.0f}, "
                        f"累计软降级={self._soft_degrade_total}次)"
                    )
                return

            if soft_action == "pause":
                if self._success_streak > 0:
                    self._success_streak = 0
                    self._pause_total += 1
                    logger.debug(
                        f"[EmbeddingBatchSizeController] 软暂停升档 "
                        f"(elapsed_ms={elapsed_ms}, p95_ms={p95:.0f}, "
                        f"ratio={ratio:.1f}x, "
                        f"累计暂停={self._pause_total}次)"
                    )
                return

            self._success_streak += 1
            if self._current_bs >= self._MAX:
                return
            if self._success_streak < self._STABLE_SAMPLES:
                return
            now = time.monotonic()
            if now - self._last_increase_ts < self._INCREASE_COOLDOWN_SEC:
                return
            old_bs = self._current_bs
            if not increase_allowed:
                self._success_streak = 0
                self._increase_pause_total += 1
                self._last_increase_pause_reason = (
                    increase_pause_reason or "increase_paused"
                )
                logger.info(
                    f"[EmbeddingBatchSizeController] bs 升档暂停 "
                    f"{old_bs} -> {old_bs} (reason={self._last_increase_pause_reason}, "
                    f"elapsed_ms={elapsed_ms}, 累计暂停={self._increase_pause_total}次)"
                )
                return
            self._current_bs = min(
                self._MAX, self._current_bs + self._INCREASE_STEP
            )
            self._last_increase_ts = now
            self._success_streak = 0
            self._increase_total += 1
            logger.info(
                f"[EmbeddingBatchSizeController] bs 上升 {old_bs} -> "
                f"{self._current_bs} (elapsed_ms={elapsed_ms}, "
                f"累计上升={self._increase_total}次)"
            )

    def record_degrade(self, reason: str) -> int:
        """GPU stall/OOM 回馈，返回砍半后的新 bs。"""
        with self._lock:
            old_bs = self._current_bs
            self._current_bs = max(self._MIN, self._current_bs // 2)
            self._success_streak = 0
            self._last_degrade_reason = reason
            self._degrade_total += 1
            if old_bs != self._current_bs:
                logger.warning(
                    f"[EmbeddingBatchSizeController] bs 砍半 {old_bs} -> "
                    f"{self._current_bs} (reason={reason}, "
                    f"累计降级={self._degrade_total}次)"
                )
            else:
                logger.warning(
                    f"[EmbeddingBatchSizeController] bs 已达下限 {self._MIN} 无法再降 "
                    f"(reason={reason}, 累计降级={self._degrade_total}次)"
                )
            return self._current_bs

    def get_stats(self) -> dict:
        with self._lock:
            p95 = self._compute_p95_unlocked()
            return {
                "current_bs": self._current_bs,
                "min_bs": self._MIN,
                "max_bs": self._MAX,
                "increase_step": self._INCREASE_STEP,
                "success_streak": self._success_streak,
                "last_degrade_reason": self._last_degrade_reason,
                "degrade_total": self._degrade_total,
                "increase_total": self._increase_total,
                "soft_degrade_total": self._soft_degrade_total,
                "pause_total": self._pause_total,
                "increase_pause_total": self._increase_pause_total,
                "last_increase_pause_reason": self._last_increase_pause_reason,
                "elapsed_window_size": len(self._elapsed_window),
                "elapsed_p95_ms": p95,
            }


_batch_size_controller: Optional[EmbeddingBatchSizeController] = None
_batch_size_controller_lock = threading.Lock()


def _get_batch_size_controller() -> EmbeddingBatchSizeController:
    """进程级单例，懒初始化。"""
    global _batch_size_controller
    if _batch_size_controller is None:
        with _batch_size_controller_lock:
            if _batch_size_controller is None:
                _batch_size_controller = EmbeddingBatchSizeController()
    return _batch_size_controller


def _reset_batch_size_controller() -> None:
    """重置单例（仅供单元测试使用）。"""
    global _batch_size_controller
    with _batch_size_controller_lock:
        _batch_size_controller = None


class GpuDeviceCooldownController:
    """GPU 设备级冷却/熔断控制器。

    与 EmbeddingBatchSizeController 的分工：
    - EmbeddingBatchSizeController: 调 GPU 上的 batch_size（工作负载层）
    - GpuDeviceCooldownController: 控制"当前是否允许把新批次投给 GPU"（设备层）

    设计要点：
    - 触发条件：bs 已降到 MIN 仍 stall/OOM、或 orphan_running 达到连续上限
    - 冷却期内所有新批次直接 CPU 回退，避免"CPU 成功后下一批又 stall"抖动
    - 到期后第一批仍尝试 GPU（probe），失败则指数退避延长冷却
    - GPU probe 成功 → 立即重置冷却状态
    - 进程级状态，线程安全；配置通过 settings 读取，允许运行时调整
    """

    def __init__(
        self,
        base_cooldown_sec: float = 60.0,
        max_cooldown_sec: float = 300.0,
        orphan_trigger_count: int = 2,
    ) -> None:
        self._lock = threading.Lock()
        self._cooldown_until: float = 0.0
        self._base_cooldown_sec: float = base_cooldown_sec
        self._max_cooldown_sec: float = max_cooldown_sec
        self._orphan_trigger_count: int = orphan_trigger_count
        self._consecutive_orphan: int = 0
        self._consecutive_trigger: int = 0
        self._total_triggered: int = 0
        self._last_trigger_reason: Optional[str] = None

    def _read_settings(self) -> None:
        """运行时从 settings 读取冷却参数，支持热调整。"""
        try:
            self._base_cooldown_sec = float(
                getattr(settings, "VECTOR_GPU_COOLDOWN_SEC", self._base_cooldown_sec)
            )
            self._max_cooldown_sec = float(
                getattr(settings, "VECTOR_GPU_COOLDOWN_MAX_SEC", self._max_cooldown_sec)
            )
            self._orphan_trigger_count = int(
                getattr(settings, "VECTOR_GPU_COOLDOWN_ORPHAN_COUNT", self._orphan_trigger_count)
            )
        except Exception:
            pass

    def is_in_cooldown(self) -> bool:
        """当前是否处于冷却期（冷却期内新批次应直接走 CPU）。"""
        with self._lock:
            return time.monotonic() < self._cooldown_until

    def remaining_cooldown_sec(self) -> float:
        with self._lock:
            return max(0.0, self._cooldown_until - time.monotonic())

    def should_probe(self) -> bool:
        """冷却刚结束时返回 True，允许下一批做 GPU probe。"""
        with self._lock:
            return self._cooldown_until > 0 and time.monotonic() >= self._cooldown_until

    def trigger(self, reason: str) -> float:
        """进入冷却；连续触发使用指数退避；返回新的冷却截止时间点（monotonic）。"""
        self._read_settings()
        with self._lock:
            self._consecutive_trigger += 1
            self._total_triggered += 1
            self._last_trigger_reason = reason
            multiplier = min(2 ** (self._consecutive_trigger - 1), 8)
            cooldown = min(self._base_cooldown_sec * multiplier, self._max_cooldown_sec)
            self._cooldown_until = time.monotonic() + cooldown
            logger.warning(
                f"[GpuDeviceCooldownController] 进入 GPU 冷却: reason={reason}, "
                f"cooldown={cooldown:.0f}s, 连续触发={self._consecutive_trigger}, "
                f"累计触发={self._total_triggered}"
            )
            return self._cooldown_until

    def record_orphan(self) -> bool:
        """记录一次 orphan_running 跳 GPU；达到阈值则进入冷却。返回是否触发冷却。"""
        self._read_settings()
        with self._lock:
            self._consecutive_orphan += 1
            if self._consecutive_orphan < self._orphan_trigger_count:
                return False
            self._consecutive_orphan = 0
        self.trigger(f"orphan_x{self._orphan_trigger_count}")
        return True

    def record_success(self) -> None:
        """GPU 成功回馈：重置连续触发计数与 orphan 计数；清除冷却状态。"""
        with self._lock:
            if self._consecutive_trigger > 0 or self._consecutive_orphan > 0 or self._cooldown_until > 0:
                logger.info(
                    f"[GpuDeviceCooldownController] GPU 恢复: "
                    f"清除连续触发={self._consecutive_trigger}, orphan={self._consecutive_orphan}"
                )
            self._consecutive_trigger = 0
            self._consecutive_orphan = 0
            self._cooldown_until = 0.0

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "cooldown_remaining_sec": max(0.0, self._cooldown_until - time.monotonic()),
                "consecutive_trigger": self._consecutive_trigger,
                "consecutive_orphan": self._consecutive_orphan,
                "total_triggered": self._total_triggered,
                "last_trigger_reason": self._last_trigger_reason,
                "base_cooldown_sec": self._base_cooldown_sec,
                "max_cooldown_sec": self._max_cooldown_sec,
            }


_device_cooldown_controller: Optional[GpuDeviceCooldownController] = None
_device_cooldown_controller_lock = threading.Lock()


def _get_device_cooldown_controller() -> GpuDeviceCooldownController:
    """进程级单例，懒初始化。"""
    global _device_cooldown_controller
    if _device_cooldown_controller is None:
        with _device_cooldown_controller_lock:
            if _device_cooldown_controller is None:
                _device_cooldown_controller = GpuDeviceCooldownController()
    return _device_cooldown_controller


def _reset_device_cooldown_controller() -> None:
    """重置单例（仅供单元测试使用）。"""
    global _device_cooldown_controller
    with _device_cooldown_controller_lock:
        _device_cooldown_controller = None


def gpu_device_stats() -> dict:
    """对外暴露设备健康快照，供观测/测试使用。"""
    return _get_device_cooldown_controller().get_stats()


def embedding_runtime_stats() -> dict:
    """暴露 embedding 模型驻留状态，供 worker 心跳与运维诊断使用。"""
    try:
        from app.core.vectorizer import embedding as embedding_module

        gpu_model = getattr(embedding_module, "_embedding_model", None)
        gpu_device = getattr(gpu_model, "device", None) if gpu_model is not None else None
    except Exception:
        gpu_model = None
        gpu_device = None

    with _cpu_model_cache_lock:
        cpu_model = _cpu_model_cache
        cpu_device = getattr(cpu_model, "device", None) if cpu_model is not None else None

    return {
        "cuda_model_loaded": gpu_model is not None,
        "cuda_model_device": gpu_device,
        "cpu_fallback_model_loaded": cpu_model is not None,
        "cpu_fallback_device": cpu_device,
    }


def _get_cpu_fallback_model():
    """获取进程级缓存的 CPU EmbeddingModel 实例。

    首次调用时构造并缓存，后续直接复用，避免每次 CPU 回退
    都重新加载 ~600MB 模型文件。
    """
    global _cpu_model_cache
    if _cpu_model_cache is None:
        with _cpu_model_cache_lock:
            if _cpu_model_cache is None:
                from app.core.vectorizer.embedding import EmbeddingModel
                logger.info("[CPU回退] 首次构建 CPU EmbeddingModel 缓存实例")
                _cpu_model_cache = EmbeddingModel(device="cpu")
    return _cpu_model_cache


async def safe_encode(
    texts: list[str],
    initial_batch_size: int = 32,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    stick_to_cpu: bool = False,
) -> np.ndarray:
    """兼容旧调用方的安全 encode 入口。

    旧实现仅处理 CUDA OOM, 无法识别 GPU stall。本实现统一复用
    ``safe_encode_batch`` 的超时、降批、CPU 兜底与设备冷却合同。

    锁策略 (新契约): ``safe_encode_batch`` 内部按设备路由自管锁,
    GPU 段用 ``encode_lock`` (串行), CPU 段用 ``cpu_encode_semaphore`` (并发);
    调用方不再需要外层持锁。

    Args:
        texts: 待向量化文本
        initial_batch_size: 上层规划上限
        progress_callback: 进度回调 (可选)
        stick_to_cpu: 显式强制走 CPU 路径; 同时本函数会自动读取
            ``_task_stick_to_cpu`` ContextVar 的任务级粘滞标志。
    """
    if not texts:
        if progress_callback is not None:
            progress_callback(0, 0)
        return np.empty((0, 0), dtype=np.float32)

    result, _meta = await safe_encode_batch(
        texts=texts,
        initial_batch_size=min(initial_batch_size, len(texts)),
        batch_no=1,
        batch_count=1,
        stick_to_cpu=stick_to_cpu,
    )

    if progress_callback is not None:
        progress_callback(len(texts), len(texts))
    return result


async def _run_in_encode_process(
    texts: list[str],
    batch_size: int,
    device: str,
    timeout: float,
) -> np.ndarray:
    """在可销毁子进程中执行 encode，超时后子进程可被强制回收。"""
    from app.core.vectorizer.encode_worker import encode_texts_in_process

    loop = asyncio.get_running_loop()
    executor = _get_encode_executor()

    try:
        future = loop.run_in_executor(
            executor,
            encode_texts_in_process,
            texts,
            batch_size,
            device,
        )
        result_list = await asyncio.wait_for(future, timeout=timeout)
        return np.array(result_list, dtype=np.float32)
    except asyncio.TimeoutError:
        logger.warning(
            f"[encode进程] 子进程超时 ({timeout:.0f}s), 重置进程池"
        )
        _reset_encode_executor()
        raise
    except concurrent.futures.process.BrokenProcessPool:
        logger.warning("[encode进程] BrokenProcessPool, 重置后重试一次")
        _reset_encode_executor()
        future = loop.run_in_executor(
            _get_encode_executor(),
            encode_texts_in_process,
            texts,
            batch_size,
            device,
        )
        result_list = await asyncio.wait_for(future, timeout=timeout)
        return np.array(result_list, dtype=np.float32)


_FALLBACK_KIND_SOFT = "soft"
_FALLBACK_KIND_HARD = "hard"


# ── 任务级 CPU 粘滞: ContextVar 实现 ─────────────────────────────────────
# 职责: 在 ARQ worker job 边界内提供 CPU 粘滞作用域 - 一次 hard CPU fallback
#       之后让本任务剩余 encode 自动走 CPU, 消除"检索/提取等多次单 query encode
#       反复 GPU stall→CPU 回退"的拉锯抖动。
# 不做: 不跨 job 边界传播 (scope 退出自动 reset); 不替代 GpuDeviceCooldownController
#       的全局熔断 (后者是进程级、wall-clock 维度); 不影响 VectorIndexer 已有
#       的显式 stick_to_cpu 参数链 (两者并存, 显式参数优先)。
_task_stick_to_cpu: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "encode_task_stick_to_cpu", default=False
)


@contextlib.contextmanager
def task_cpu_sticky_scope() -> Iterator[None]:
    """任务作用域: 进入时重置粘滞标志为 False, 退出时自动恢复父作用域。

    使用模式: 在 ARQ worker job 入口套一层 with 包裹整个任务执行;
    scope 内任一 encode 一旦因 hard fallback 触发粘滞, 同 scope 内
    后续所有 encode 直接走 CPU; scope 退出后自动清理, 不污染其他 job。
    """
    token = _task_stick_to_cpu.set(False)
    try:
        yield
    finally:
        _task_stick_to_cpu.reset(token)


def _mark_task_sticky_cpu() -> None:
    """在当前 ContextVar 作用域内将粘滞标志置 True (仅供 safe_encode_batch 内部回写)。"""
    _task_stick_to_cpu.set(True)


def is_task_cpu_sticky() -> bool:
    """对外暴露当前任务粘滞标志, 供观测/测试使用。"""
    return _task_stick_to_cpu.get()


# ── encode 子系统优雅关闭: in-flight 跟踪 + 拒绝新提交 ─────────────────
# 职责: 在 ARQ worker shutdown 钩子中协助优雅关闭 encode 子系统 - 跟踪
#       in-flight encode 数量, shutdown 触发后拒绝新提交, 等 in-flight
#       完成 (或超时) 再关闭底层 executor, 让业务日志在 [Worker] 已关闭
# 之前出现。
# 不做: 不强杀 thread (Python ThreadPoolExecutor 标准限制, 运行中 thread
#       由 Python atexit 自然等); 不调 os._exit; 不替代 ARQ 内置的
#       job_completion_wait (后者负责 asyncio.Task 层取消, 本机制负责
#       底层 thread/process 资源层等待)。
_encode_shutdown_lock = threading.Lock()
_encode_shutting_down: bool = False
_active_encode_count: int = 0
_all_idle_event: Optional[asyncio.Event] = None


class EncodeShuttingDownError(RuntimeError):
    """shutdown 期间被拒绝的新 encode 提交; 调用方应当作普通失败处理。"""


def _get_all_idle_event() -> asyncio.Event:
    """懒初始化进程级 idle event, 初始状态按当前 in-flight 计数决定。

    asyncio.Event 必须绑定当前事件循环, 因此在首次 inc 或 shutdown 时构造。
    初始状态: in-flight=0 时 set (idle), 否则 clear (busy)。
    """
    global _all_idle_event
    with _encode_shutdown_lock:
        if _all_idle_event is None:
            _all_idle_event = asyncio.Event()
            if _active_encode_count == 0:
                _all_idle_event.set()
        return _all_idle_event


def _inc_active_encode() -> None:
    """记录一个 in-flight encode 进入; 计数从 0 升起时 clear idle event。

    必须确保 idle event 已存在 (绑定到当前 loop), 否则 shutdown 看不到 busy 状态。
    """
    _get_all_idle_event()
    global _active_encode_count
    with _encode_shutdown_lock:
        _active_encode_count += 1
        if _active_encode_count == 1 and _all_idle_event is not None:
            _all_idle_event.clear()


def _dec_active_encode() -> None:
    """记录一个 in-flight encode 完成; 计数归零时 set idle event 唤醒 shutdown 等待。"""
    global _active_encode_count
    with _encode_shutdown_lock:
        if _active_encode_count > 0:
            _active_encode_count -= 1
        if _active_encode_count == 0 and _all_idle_event is not None:
            _all_idle_event.set()


def _check_not_shutting_down() -> None:
    """safe_encode_batch 入口拒绝 shutdown 期间的新提交。

    职责: 让 worker shutdown 后到达的 encode 调用快速失败, 不再增加 in-flight
    不做: 不区分 GPU/CPU 路径; 不写降级 metric (调用方按异常处理即可)
    """
    with _encode_shutdown_lock:
        if _encode_shutting_down:
            raise EncodeShuttingDownError(
                "encode 子系统正在关闭, 拒绝新提交"
            )


def get_active_encode_count() -> int:
    """对外暴露当前 in-flight encode 数量, 供观测/测试使用。"""
    with _encode_shutdown_lock:
        return _active_encode_count


def is_encode_shutting_down() -> bool:
    """对外暴露 shutdown 标志, 供观测/测试使用。"""
    with _encode_shutdown_lock:
        return _encode_shutting_down


async def shutdown_encode_workloads(timeout: float = 15.0) -> dict:
    """优雅关闭 encode 子系统。

 职责: 拒绝新 encode 提交, 等 in-flight 完成 (或超时), 关闭底层 executor
 不做: 不强杀 thread (Python ThreadPoolExecutor 标准限制, 运行中 thread
 由 Python atexit 自然等); 不调 os._exit; 不替代 ARQ 内置的
 job_completion_wait (后者负责 asyncio.Task 层取消)

 调用模式 (worker.py shutdown 钩子):
 stats = await shutdown_encode_workloads(timeout=15.0)
 if stats["timeout"]:
 logger.warning("encode in-flight 未在 grace 内完成: %s", stats)

 Returns:
 dict: {
 "timeout": bool, # 是否触发超时强制关闭
 "active_count_at_exit": int, # 退出时仍 in-flight 的 encode 数
 "active_count_at_start": int, # 进入 shutdown 时的 in-flight 数
 "elapsed_sec": float, # 实际等待耗时
 }
 """
    global _encode_shutting_down
    t0 = time.monotonic()

    # 第 1 步: 标记 shutdown, 后续 safe_encode_batch 入口会 raise EncodeShuttingDownError
    with _encode_shutdown_lock:
        was_already_shutting_down = _encode_shutting_down
        _encode_shutting_down = True
        active_at_start = _active_encode_count

    if was_already_shutting_down:
        logger.info(
            f"[encode shutdown] 已处于 shutdown 状态, 直接返回 "
            f"(in-flight={active_at_start})"
        )
        return {
            "timeout": False,
            "active_count_at_exit": active_at_start,
            "active_count_at_start": active_at_start,
            "elapsed_sec": 0.0,
            "already_shutdown": True,
        }

    logger.info(
        f"[encode shutdown] 进入优雅关闭: in-flight={active_at_start}, "
        f"grace={timeout:.1f}s"
    )

    # 第 2 步: 等待 in-flight 清零, 或超时
    timed_out = False
    if active_at_start > 0:
        try:
            idle_event = _get_all_idle_event()
            await asyncio.wait_for(idle_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True

    elapsed_sec = time.monotonic() - t0
    with _encode_shutdown_lock:
        active_at_exit = _active_encode_count

    # 第 3 步: 关闭底层 executor
    #   - GPU ThreadPool: cancel 排队 future, 运行中的 thread 由 Python atexit 自然等
    #   - 子进程 ProcessPool: cancel 排队 future, 子进程随 worker 进程退出被回收
    #   - CPU 段 asyncio.to_thread 用默认 thread pool, 不在此关闭
    global _gpu_encode_executor, _encode_executor
    with _gpu_encode_executor_lock:
        if _gpu_encode_executor is not None:
            try:
                _gpu_encode_executor.shutdown(wait=False, cancel_futures=True)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[encode shutdown] GPU executor shutdown 异常 (忽略): {e}")
    with _encode_executor_lock:
        if _encode_executor is not None:
            try:
                _encode_executor.shutdown(wait=False, cancel_futures=True)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[encode shutdown] 子进程 executor shutdown 异常 (忽略): {e}")

    if timed_out:
        logger.warning(
            f"[encode shutdown] 超时未清零: active_at_exit={active_at_exit}, "
            f"elapsed={elapsed_sec:.1f}s, 已强制 cancel 排队 future "
            f"(运行中 thread 由 Python atexit 等待)"
        )
    else:
        logger.info(
            f"[encode shutdown] 已优雅退出: active_at_exit={active_at_exit}, "
            f"elapsed={elapsed_sec:.1f}s"
        )

    return {
        "timeout": timed_out,
        "active_count_at_exit": active_at_exit,
        "active_count_at_start": active_at_start,
        "elapsed_sec": round(elapsed_sec, 3),
    }


def _reset_encode_shutdown_state_for_tests() -> None:
    """重置 shutdown 状态 (仅供单元测试使用), 不影响生产语义。"""
    global _encode_shutting_down, _active_encode_count, _all_idle_event
    with _encode_shutdown_lock:
        _encode_shutting_down = False
        _active_encode_count = 0
        _all_idle_event = None


def _admission_batch_candidates(effective_bs: int, min_bs: int) -> list[int]:
    """Return bounded local batch-size probes for GPU admission.

    Admission failures are preflight failures, not real GPU execution failures,
    so this sequence must not mutate the global AIMD controller.
    """

    first = max(1, int(effective_bs or 1))
    floor = max(1, int(min_bs or 1))
    candidates: list[int] = [first]
    for candidate in (8, floor):
        candidate = max(1, int(candidate))
        if floor <= candidate < first and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _cuda_embedding_model_loaded() -> bool:
    """Return whether this process already holds a CUDA embedding model."""

    try:
        from app.core.vectorizer import embedding as embedding_module

        model = getattr(embedding_module, "_embedding_model", None)
        if model is None:
            return False
        return str(getattr(model, "device", "")).lower().startswith("cuda")
    except Exception:
        return False


async def safe_encode_batch(
    texts: list[str],
    initial_batch_size: int = 32,
    batch_no: int = 0,
    batch_count: int = 0,
    stick_to_cpu: bool = False,
    admission_fail_streak: int = 0,
    prefer_subprocess: bool = False,
) -> tuple[np.ndarray, dict]:
    """面向文档主链路的单批安全 encode, GPU 串行 + CPU 并发的双锁路由。

 锁策略（新契约, 第三层改造）:
 - GPU 段持有 ``encode_lock`` (asyncio.Lock, 全进程串行单 GPU)
 - CPU 段持有 ``cpu_encode_semaphore`` (容量来自 cpu_budget.max_concurrency)
 - 调用方不再需要外层持锁; 函数自身按设备路由选锁

 GPU 段（单次尝试 + AIMD + 设备熔断语义）:
 - 入口 effective_bs = min(上层规划, controller.current_bs, len(texts))
 - 仅做 1 次 GPU 尝试 (历史的 3 次重试被取消; 实测重试无法缓解 GPU 拥堵,
 反而每次 attempt 都会 stash 一个 orphan future 拖累后续批次)
 - 成功 → controller.record_success → return
 - stall/OOM → controller.record_degrade (bs//=2, 让下批次起步更小)
 + device_cooldown.trigger ("stall_no_retry" / "oom_no_retry") 立即熔断
 → 直接退出 GPU 段进入 CPU 兜底

 CPU 段（第二/三层）:
 - prefer_subprocess=False(默认): 主进程线程 CPU → 失败再走子进程 CPU
 - prefer_subprocess=True(vectorize 大批量): 子进程 CPU 主路径 → 失败回退线程
 (: 子进程 encode 不占主进程 GIL, 避免饿死向量化 keepalive 协程
 导致 Redis 心跳停滞被 reconciler 误判 running_heartbeat_stale 重复恢复)
 - 在 cpu_encode_semaphore 内执行, 多路并发
 - 线程模式成功时把 latency/queue_wait 度量投递给 CpuEncodeMetric handler

 前置检查（不持锁, 优先级从高到低）:
 - settings.EMBEDDING_DEVICE == "cpu" → CPU 主路径 (部署层显式指定),
 整段 GPU 段跳过, 不算 fallback, 不写 ContextVar 粘滞
 - stick_to_cpu=True 或 ContextVar 粘滞 → 主动 CPU 路径, 直接 CPU
 - 前批孤儿 encode 仍在跑 → 直接 CPU; 累计达阈值触发 device_cooldown
 - device_cooldown.is_in_cooldown → 直接 CPU

 CPU 段日志语义 (修复):
 - cpu_main_path → DEBUG "CPU 主路径编码完成" (部署层意图, 业务无需感知)
 - stick_to_cpu/task_sticky_cpu/device_cooldown → INFO "CPU 路径编码完成 (reason=...)"
 - gpu_admission:insufficient_vram (soft) → INFO "GPU 容量不足, CPU 兜底"
 - stall/oom/orphan_running/repeated_admission_fail (hard) → WARNING "GPU 异常, CPU 兜底"

 Args:
 texts: 本批待向量化文本
 initial_batch_size: 上层规划上限
 batch_no / batch_count: 用于日志与进度标注
 stick_to_cpu: 显式强制走 CPU 路径 (优先级低于 cpu_main_path)。
 同时本函数会自动读取 ``_task_stick_to_cpu`` ContextVar,
 任一为 True 即视为粘滞生效;
 hard fallback 后自动回写 ContextVar, 同一 worker scope 内
 后续 encode 自动跳 GPU。
 注: cpu_main_path 主路径下不写 ContextVar, 无需粘滞。
 prefer_subprocess: CPU 段优先用子进程池 encode(不占主进程 GIL)。
 由 vectorize 大批量主链路(VectorIndexer.index_chunks)传 True,
 根除"CPU encode 持 GIL 阻塞事件循环饿死 keepalive"的;
 检索/单条 encode 默认 False, 保持线程主路径避免逐条跨进程开销。
 子进程不可用时自动回退线程, 两路互为兜底。

 Raises:
 EncodeShuttingDownError: shutdown_encode_workloads 已被触发,
 拒绝新提交。调用方按普通异常处理即可。
 """
    _check_not_shutting_down()
    controller = _get_batch_size_controller()
    device_cooldown = _get_device_cooldown_controller()
    # 优先级: EMBEDDING_DEVICE=cpu 主路径 > 显式 stick_to_cpu > ContextVar 粘滞 > 普通路径
    # cpu_main_path = True 表示部署层 (docker-compose / settings) 已显式指定该进程
    # 走 CPU 主路径 (例如 worker-llm 容器); 此时整段 GPU 逻辑/锁/admission 应全部跳过,
    # 不应再产生 "GPU 失败 / CPU 回退" 语义的日志和 fallback meta。
    cpu_main_path = (settings.EMBEDDING_DEVICE or "auto").lower() == "cpu"
    context_stick = _task_stick_to_cpu.get()
    effective_stick = bool(stick_to_cpu) or context_stick
    effective_bs = min(
        initial_batch_size, controller.current_bs, len(texts)
    )
    meta: dict = {
        "device": "gpu",
        "degraded_to_cpu": False,
        "fallback_reason": None,
        "recovery_mode": "thread",
        "retry_chain": [],
        "cpu_timeout_sec": 0,
        "device_cooldown_sec": 0.0,
        "stick_to_cpu": bool(stick_to_cpu),
        "context_stick_to_cpu": bool(context_stick),
        "gpu_lock_wait_ms": 0,
        "cpu_queue_wait_ms": 0,
        "cpu_fallback_kind": None,
        "sticky_cpu_eligible": False,
        "admission_failed": False,
        "admission_fail_streak": int(admission_fail_streak or 0),
        "cpu_main_path": cpu_main_path,
    }

    skip_gpu = False
    skip_reason: Optional[str] = None

    # ── 前置检查 -1: 配置驱动的 CPU 主路径 (优先级最高) ──
    # 该容器/进程的设计就是 CPU 主路径, 跳过 GPU 段不算 fallback。
    # 不写 ContextVar 粘滞 (本来就是主路径, 无需粘滞); meta 不标记 degraded_to_cpu。
    if cpu_main_path:
        skip_gpu = True
        skip_reason = "cpu_main_path"
        meta["retry_chain"].append({
            "batch_size": effective_bs, "result": "skip_gpu",
            "reason": skip_reason,
        })
        meta["sticky_cpu_eligible"] = False
        meta["cpu_fallback_kind"] = None
    # ── 前置检查 0: 任务级粘滞 CPU (显式形参 或 ContextVar 任一生效) ──
    # 粘滞自愈探查: 已粘滞时不再无条件 skip_gpu, 先用零成本 vram
    # snapshot 判定显存是否已恢复; 恢复则本批尝试 GPU, GPU 真正成功后才在
    # record_success 分支复位 ContextVar。仍在冷却期或显存不足时维持零成本 CPU。
    sticky_probe_attempted = False
    if (
        effective_stick
        and not device_cooldown.is_in_cooldown()
        and meta.get("cpu_fallback_kind") is None
    ):
        from app.core.gpu.admission import (
            estimate_embedding_required_mb,
            vram_allows_gpu_retry,
        )
        if vram_allows_gpu_retry(
            estimate_embedding_required_mb(texts, effective_bs)
        ):
            sticky_probe_attempted = True
            meta["sticky_probe"] = True
            meta["retry_chain"].append({
                "batch_size": effective_bs, "result": "sticky_probe",
                "reason": "vram_recovered",
            })
            logger.info(
                f"[safe_encode_batch] 粘滞自愈探查: 显存已恢复, "
                f"本批尝试 GPU: batch={batch_no}/{batch_count}, bs={effective_bs}"
            )

    if effective_stick and not sticky_probe_attempted:
        skip_gpu = True
        # 区分粘滞来源, 便于日志/观测定位 (显式调用方 vs 任务作用域传播)
        skip_reason = "stick_to_cpu" if stick_to_cpu else "task_sticky_cpu"
        # task_sticky_cpu 生效时降为 DEBUG 避免每个子查询都打 INFO 噪音;
        # 显式 stick_to_cpu 仍 INFO (调用方有意为之, 值得留痕)。
        if stick_to_cpu:
            logger.info(
                f"[safe_encode_batch] {skip_reason} 生效, 本批直接 CPU: "
                f"batch={batch_no}/{batch_count}, bs={effective_bs}"
            )
        else:
            logger.debug(
                f"[safe_encode_batch] {skip_reason} 生效, 本批直接 CPU: "
                f"batch={batch_no}/{batch_count}, bs={effective_bs}"
            )
        meta["retry_chain"].append({
            "batch_size": effective_bs, "result": "skip_gpu",
            "reason": skip_reason,
        })
        meta["cpu_fallback_kind"] = _FALLBACK_KIND_HARD
        meta["sticky_cpu_eligible"] = True

    # ── 前置检查 1: 前批孤儿仍在占 GPU ──
    if not skip_gpu and _orphan_still_running():
        skip_gpu = True
        skip_reason = "orphan_running"
        logger.warning(
            f"[safe_encode_batch] 前批孤儿 encode 仍未完成, "
            f"本批直接 CPU 回退: batch={batch_no}/{batch_count}, bs={effective_bs}"
        )
        meta["retry_chain"].append({
            "batch_size": effective_bs, "result": "skip_gpu",
            "reason": skip_reason,
        })
        meta["cpu_fallback_kind"] = _FALLBACK_KIND_HARD
        meta["sticky_cpu_eligible"] = True
        if device_cooldown.record_orphan():
            meta["device_cooldown_sec"] = round(
                device_cooldown.remaining_cooldown_sec(), 1
            )

    # ── 前置检查 2: 设备级冷却中 ──
    if not skip_gpu and device_cooldown.is_in_cooldown():
        remaining = device_cooldown.remaining_cooldown_sec()
        skip_gpu = True
        skip_reason = "device_cooldown"
        logger.info(
            f"[safe_encode_batch] GPU 冷却中({remaining:.0f}s), "
            f"本批直接 CPU: batch={batch_no}/{batch_count}, bs={effective_bs}"
        )
        meta["retry_chain"].append({
            "batch_size": effective_bs, "result": "skip_gpu",
            "reason": skip_reason,
            "cooldown_remaining_sec": round(remaining, 1),
        })
        meta["cpu_fallback_kind"] = _FALLBACK_KIND_HARD
        meta["sticky_cpu_eligible"] = True
        meta["device_cooldown_sec"] = round(remaining, 1)

    # ── GPU 段: 持 encode_lock 串行执行 ──
    if not skip_gpu:
        gpu_lock = get_encode_lock()
        gpu_wait_start = time.perf_counter()
        async with gpu_lock:
            meta["gpu_lock_wait_ms"] = int(
                (time.perf_counter() - gpu_wait_start) * 1000
            )
            from app.core.gpu.admission import (
                estimate_embedding_required_mb,
                gpu_admission_context,
            )

            admission_candidates = _admission_batch_candidates(
                effective_bs, controller.min_bs
            )
            final_admission_reason: Optional[str] = None
            for candidate_index, admission_bs in enumerate(admission_candidates):
                estimated_required_mb = estimate_embedding_required_mb(
                    texts, admission_bs
                )
                async with gpu_admission_context(
                    kind="embedding",
                    estimated_required_mb=estimated_required_mb,
                    owner=f"batch={batch_no}/{batch_count}:bs={admission_bs}",
                    resident_model_loaded=_cuda_embedding_model_loaded(),
                ) as admission:
                    snapshot = admission.snapshot
                    admission_meta = {
                        "action": admission.action,
                        "reason": admission.reason,
                        "batch_size": admission_bs,
                        "estimated_required_mb": admission.estimated_required_mb,
                        "reserve_mb": admission.min_free_mb,
                        "required_free_mb": admission.required_free_mb,
                        "free_mb": snapshot.free_mb if snapshot else None,
                        "source": snapshot.source if snapshot else None,
                    }
                    meta["gpu_admission"] = admission_meta
                    final_admission_reason = admission.reason
                    if admission.action != "use_gpu":
                        reason = f"gpu_admission:{admission.reason}"
                        is_last_candidate = (
                            candidate_index == len(admission_candidates) - 1
                        )
                        resident_model_admitted = admission.reason == "resident_model_loaded"
                        can_retry_smaller = (
                            admission.reason == "insufficient_vram"
                            and not is_last_candidate
                            and not resident_model_admitted
                        )
                        logger.info(
                            f"[safe_encode_batch] GPU admission "
                            f"{'降批重估' if can_retry_smaller else '跳过 GPU'}: "
                            f"batch={batch_no}/{batch_count}, bs={admission_bs}, "
                            f"reason={admission.reason}, "
                            f"estimated_mb={admission.estimated_required_mb}, "
                            f"reserve_mb={admission.min_free_mb}, "
                            f"required_free_mb={admission.required_free_mb}, "
                            f"free_mb={snapshot.free_mb if snapshot else 'unknown'}, "
                            f"util={snapshot.utilization if snapshot else 'unknown'}"
                        )
                        meta["retry_chain"].append({
                            "batch_size": admission_bs,
                            "result": (
                                "admission_reject"
                                if can_retry_smaller
                                else "skip_gpu"
                            ),
                            "reason": reason,
                            "estimated_required_mb": admission.estimated_required_mb,
                            "reserve_mb": admission.min_free_mb,
                            "required_free_mb": admission.required_free_mb,
                            "free_mb": snapshot.free_mb if snapshot else None,
                        })
                        if can_retry_smaller:
                            continue

                        skip_gpu = True
                        skip_reason = reason
                        if admission.reason == "inflight_full":
                            # 许可与设备占用脱节是瞬时争用, 不得累加显存失败streak, 也不得粘滞。
                            meta["cpu_fallback_kind"] = _FALLBACK_KIND_SOFT
                            meta["sticky_cpu_eligible"] = False
                        elif admission.reason == "insufficient_vram":
                            next_streak = int(admission_fail_streak or 0) + 1
                            threshold = max(
                                1,
                                int(getattr(
                                    settings,
                                    "GPU_ADMISSION_REPEATED_FAIL_THRESHOLD",
                                    3,
                                )),
                            )
                            meta["admission_failed"] = True
                            meta["admission_fail_streak"] = next_streak
                            meta["admission_fail_threshold"] = threshold
                            if next_streak >= threshold:
                                skip_reason = "repeated_admission_fail"
                                meta["retry_chain"][-1]["reason"] = skip_reason
                                meta["retry_chain"][-1]["admission_reason"] = reason
                                meta["cpu_fallback_kind"] = _FALLBACK_KIND_HARD
                                meta["sticky_cpu_eligible"] = True
                            else:
                                meta["cpu_fallback_kind"] = _FALLBACK_KIND_SOFT
                                meta["sticky_cpu_eligible"] = False
                        else:
                            meta["cpu_fallback_kind"] = _FALLBACK_KIND_HARD
                            meta["sticky_cpu_eligible"] = True
                        break

                    effective_bs = admission_bs
                    from app.core.vectorizer.embedding import get_embedding_model

                    embedding_model = get_embedding_model()
                    # 设备标签取模型真实所在设备。torch 被装成 CPU-only
                    # 构建时模型会落在 CPU, 此时仍把本段标成 device=gpu 会让日志与
                    # 指标谎报, 掩盖"装错 torch 包"这类环境故障。
                    actual_device = (
                        "gpu"
                        if str(getattr(embedding_model, "device", ""))
                        .lower()
                        .startswith("cuda")
                        else "cpu"
                    )
                    if actual_device == "cpu":
                        meta["device"] = "cpu"
                        meta["gpu_segment_on_cpu"] = True
                    # 单次 GPU 尝试: stall/OOM 不再重试 (实测重试无法缓解 GPU 拥堵,
                    # 反而每次 attempt 都会 stash 一个 orphan future 拖累后续批次)。
                    # AIMD 砍半仍保留 -> 让下一批次起步更小;
                    # 设备冷却首次失败即触发 -> 让本进程其他批次直接 CPU。
                    attempt_bs = effective_bs
                    first_timeout = _get_adaptive_stall_timeout(
                        attempt_bs,
                        texts=texts,
                    )
                    t0 = time.perf_counter()
                    gpu_observation = OffloadObservation()
                    gpu_future: Optional["asyncio.Future"] = None
                    try:
                        loop = asyncio.get_running_loop()
                        total_chars = sum(len(text or "") for text in texts)
                        gpu_future = loop.run_in_executor(
                            _get_gpu_encode_executor(),
                            observed_call(embedding_model.encode, gpu_observation),
                            texts,
                            attempt_bs,
                            False,
                            None,
                        )
                        # 跟踪 in-flight: future 完成 (含 stall stash 为 orphan
                        # 后底层 thread 跑完) 时自动 dec; 优雅关闭依赖
                        _inc_active_encode()
                        gpu_future.add_done_callback(
                            lambda _f: _dec_active_encode()
                        )
                        # 墙钟超时若只是调度延迟, wait_for_offload 会再等一个窗口;
                        # 只有设备内预算用尽才抛 TimeoutError。
                        result = await wait_for_offload(
                            gpu_future,
                            first_timeout,
                            gpu_observation,
                        )
                        device_elapsed = gpu_observation.device_elapsed_sec
                        elapsed = (
                            device_elapsed
                            if device_elapsed is not None
                            else time.perf_counter() - t0
                        )
                        elapsed_ms = int(elapsed * 1000)
                        if device_elapsed is not None:
                            _record_batch_duration(device_elapsed)
                        watermark_meta: dict = {
                            "allow_increase": True,
                            "reason": "not_evaluated",
                        }
                        try:
                            from app.core.vectorizer.gpu_watermark import (
                                should_allow_aimd_increase,
                            )

                            controller_stats = controller.get_stats()
                            candidate_bs = min(
                                int(initial_batch_size or 1),
                                len(texts),
                                int(controller_stats.get("max_bs") or attempt_bs),
                                int(controller_stats.get("current_bs") or attempt_bs)
                                + int(controller_stats.get("increase_step") or 1),
                            )
                            watermark_decision = should_allow_aimd_increase(
                                texts=texts,
                                candidate_batch_size=max(1, candidate_bs),
                            )
                            if hasattr(watermark_decision, "to_dict"):
                                watermark_meta = watermark_decision.to_dict()
                            else:
                                watermark_meta = dict(vars(watermark_decision))
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                f"[safe_encode_batch] GPU AIMD 水位判断失败, "
                                f"默认允许升档: {exc}"
                            )
                            watermark_meta = {
                                "allow_increase": True,
                                "reason": "watermark_error_allow",
                            }
                        meta["gpu_aimd_watermark"] = watermark_meta
                        controller.record_success(
                            elapsed_ms,
                            increase_allowed=bool(
                                watermark_meta.get("allow_increase", True)
                            ),
                            increase_pause_reason=str(
                                watermark_meta.get("reason") or "unknown"
                            ),
                        )
                        device_cooldown.record_success()
                        # 粘滞自愈成功: 清 ContextVar 并标记, 让 indexer
                        # 同步复位 task_stick_to_cpu。effective_stick 同时覆盖显式
                        # 形参与任务级粘滞, 两者一并复位。
                        if effective_stick:
                            try:
                                _task_stick_to_cpu.set(False)
                                meta["sticky_cpu_cleared"] = True
                                logger.info(
                                    f"[safe_encode_batch] 粘滞自愈: GPU 复用成功, "
                                    f"已清除任务级 CPU 粘滞: "
                                    f"batch={batch_no}/{batch_count}"
                                )
                            except Exception as _exc:
                                logger.debug(
                                    f"[safe_encode_batch] 粘滞清除异常(忽略): {_exc}"
                                )
                        meta["retry_chain"].append({
                            "batch_size": attempt_bs, "result": "ok",
                            "elapsed_ms": elapsed_ms, "attempt": 1,
                            "gpu_aimd_watermark": watermark_meta,
                        })
                        wall_ms = int((time.perf_counter() - t0) * 1000)
                        logger.info(
                            f"[safe_encode_batch] batch={batch_no}/{batch_count}, "
                            f"bs={attempt_bs}, elapsed_ms={elapsed_ms}, "
                            f"wall_ms={wall_ms}, "
                            f"device={actual_device}, mode=thread, attempt=1"
                        )
                        vector_encode_batch_total.labels(flow="vectorize", device=actual_device, result="ok").inc()
                        vector_encode_batch_chars.labels(flow="vectorize", device=actual_device).observe(total_chars)
                        vector_encode_latency_seconds.labels(flow="vectorize", device=actual_device, mode="thread").observe(elapsed)
                        return result, meta

                    except asyncio.TimeoutError:
                        device_elapsed = gpu_observation.device_elapsed_sec
                        elapsed = (
                            device_elapsed
                            if device_elapsed is not None
                            else time.perf_counter() - t0
                        )
                        meta["retry_chain"].append({
                            "batch_size": attempt_bs, "result": "stall",
                            "elapsed_ms": int(elapsed * 1000),
                            "attempt_timeout_sec": round(first_timeout, 1),
                            "attempt": 1,
                            "timeout_cause": "device_stall",
                        })
                        meta["cpu_fallback_kind"] = _FALLBACK_KIND_HARD
                        meta["sticky_cpu_eligible"] = True
                        if gpu_future is not None and not gpu_future.done():
                            _stash_orphan(gpu_future)
                        logger.warning(
                            f"[safe_encode_batch] GPU stall (no retry): "
                            f"batch={batch_no}/{batch_count}, bs={attempt_bs}, "
                            f"timeout={first_timeout:.0f}s, 直接进入 CPU 兜底"
                        )
                        await _try_clear_cuda_cache_async()
                        controller.record_degrade("stall")
                        device_cooldown.trigger("stall_no_retry")
                        meta["device_cooldown_sec"] = round(
                            device_cooldown.remaining_cooldown_sec(), 1
                        )

                    except Exception as e:
                        elapsed = time.perf_counter() - t0
                        if _is_cuda_oom(e):
                            meta["retry_chain"].append({
                                "batch_size": attempt_bs, "result": "oom",
                                "elapsed_ms": int(elapsed * 1000),
                                "attempt_timeout_sec": round(first_timeout, 1),
                                "attempt": 1,
                            })
                            meta["cpu_fallback_kind"] = _FALLBACK_KIND_HARD
                            meta["sticky_cpu_eligible"] = True
                            logger.warning(
                                f"[safe_encode_batch] CUDA OOM (no retry): "
                                f"batch={batch_no}/{batch_count}, bs={attempt_bs}, "
                                f"直接进入 CPU 兜底"
                            )
                            await _try_clear_cuda_cache_async()
                            controller.record_degrade("oom")
                            device_cooldown.trigger("oom_no_retry")
                            meta["device_cooldown_sec"] = round(
                                device_cooldown.remaining_cooldown_sec(), 1
                            )
                        else:
                            raise
                    break

            if skip_gpu and final_admission_reason == "insufficient_vram":
                # Admission-only fallback is a preflight result; it must not
                # degrade the AIMD batch controller unless repeated failures
                # have been escalated by the caller-provided streak.
                pass

    # GPU 锁已释放; 此后所有逻辑走 CPU 信号量

    # ── CPU 段: 持 cpu_encode_semaphore 并发执行 ──
    fallback_reasons = set()
    for entry in meta["retry_chain"]:
        if entry["result"] in ("stall", "oom"):
            fallback_reasons.add(entry["result"])
        elif entry["result"] == "skip_gpu":
            fallback_reasons.add(entry.get("reason") or "skip_gpu")
    reason_str = "+".join(sorted(fallback_reasons)) or "unknown"

    # cpu_main_path: 部署层 (EMBEDDING_DEVICE=cpu) 显式指定 CPU 主路径,
    # 既不是 fallback 也不是 degrade, 仅是按配置走的"正常 CPU 路径";
    # meta 不标记 degraded_to_cpu, cpu_fallback_kind 保持 None。
    if cpu_main_path:
        meta["device"] = "cpu"
        meta["recovery_mode"] = "thread"
    else:
        if meta.get("cpu_fallback_kind") is None:
            hard_reasons = {
                "stall",
                "oom",
                "stick_to_cpu",
                "task_sticky_cpu",
                "orphan_running",
                "device_cooldown",
                "repeated_admission_fail",
            }
            if fallback_reasons and fallback_reasons.issubset(
                {"gpu_admission:insufficient_vram", "gpu_admission:inflight_full"}
            ):
                meta["cpu_fallback_kind"] = _FALLBACK_KIND_SOFT
                meta["sticky_cpu_eligible"] = False
            elif fallback_reasons & hard_reasons:
                meta["cpu_fallback_kind"] = _FALLBACK_KIND_HARD
                meta["sticky_cpu_eligible"] = True
            else:
                meta["cpu_fallback_kind"] = _FALLBACK_KIND_HARD
                meta["sticky_cpu_eligible"] = True

        meta["degraded_to_cpu"] = True
        meta["fallback_reason"] = reason_str
        meta["device"] = "cpu"
        meta["recovery_mode"] = "thread"

        # 任务级 CPU 粘滞: hard fallback 时回写 ContextVar, 让同 scope 后续 encode
        # 自动跳 GPU; admission soft fallback (sticky_cpu_eligible=False) 不触发,
        # 避免单次预检失败污染整任务。cpu_main_path 时不触发粘滞 (主路径不需要)。
        if meta.get("sticky_cpu_eligible") and not _task_stick_to_cpu.get():
            _mark_task_sticky_cpu()
            logger.info(
                f"[safe_encode_batch] 任务级 CPU 粘滞已开启 (via ContextVar): "
                f"batch={batch_no}/{batch_count}, reason={reason_str}"
            )

    # 入口日志: 按 "主路径 / 主动 CPU / soft 兜底 / hard 异常" 四分语义
    _SOFT_REASONS = {
        "gpu_admission:insufficient_vram",
        "gpu_admission:inflight_full",
    }
    _INTENDED_CPU_REASONS = {
        "stick_to_cpu",
        "task_sticky_cpu",
        "device_cooldown",
    }
    if cpu_main_path:
        # 部署层主路径, 业务无需感知, 降为 DEBUG 减噪
        logger.debug(
            f"[safe_encode_batch] CPU 主路径执行: "
            f"batch={batch_no}/{batch_count}, bs={effective_bs}"
        )
    elif fallback_reasons and fallback_reasons.issubset(_INTENDED_CPU_REASONS):
        # 任务级粘滞 / 显式 stick / 设备冷却: 主动绕开 GPU, INFO 即可
        logger.info(
            f"[safe_encode_batch] CPU 路径执行 (reason={reason_str}): "
            f"batch={batch_no}/{batch_count}, bs={effective_bs}"
        )
    elif fallback_reasons and fallback_reasons.issubset(_SOFT_REASONS):
        # admission 预检软回退: 不粘滞, 下一批仍可再试 GPU
        if fallback_reasons == {"gpu_admission:inflight_full"}:
            logger.info(
                f"[safe_encode_batch] GPU 许可与设备占用不一致, 本批 CPU 兜底 "
                f"(reason={reason_str}): batch={batch_no}/{batch_count}"
            )
        else:
            logger.info(
                f"[safe_encode_batch] GPU 容量不足, 本批 CPU 兜底 (reason={reason_str}): "
                f"batch={batch_no}/{batch_count}"
            )
    else:
        # 真异常 (stall/oom/orphan_running/repeated_admission_fail 等): WARNING
        logger.warning(
            f"[safe_encode_batch] GPU 异常 (reason={reason_str}), "
            f"batch={batch_no}/{batch_count}, CPU 兜底"
        )

    cpu_bs = min(16, len(texts))
    cpu_timeout = estimate_cpu_fallback_timeout(texts)
    meta["cpu_timeout_sec"] = round(cpu_timeout, 1)
    total_chars_cpu = sum(len(text or "") for text in texts)

    cpu_sem = _get_cpu_encode_semaphore()
    cpu_queue_wait_start = time.perf_counter()
    async with cpu_sem:
        queue_wait_sec = time.perf_counter() - cpu_queue_wait_start
        meta["cpu_queue_wait_ms"] = int(queue_wait_sec * 1000)
        try:
            vector_cpu_queue_wait_seconds.observe(queue_wait_sec)
            vector_cpu_inflight_gauge.inc()
        except Exception:  # noqa: BLE001
            pass

        # 跟踪 in-flight: 优雅关闭依赖, finally 块负责 dec
        _inc_active_encode()
        try:
            # CPU encode 双模式 —— thread(复用缓存模型, 但持 GIL 期间阻塞事件
            # 循环) 与 process(encode 离开主进程, 不占 GIL, 保活 keepalive)。vectorize
            # 大批量走子进程主路径根除"CPU encode 饿死心跳 -> reconciler 误判重复恢复",
            # 检索/提取小批量保持线程主路径避免逐条跨进程开销。两路互为兜底。
            cpu_observation = OffloadObservation()

            async def _cpu_encode_thread() -> np.ndarray:
                cpu_model = _get_cpu_fallback_model()
                cpu_future = asyncio.get_running_loop().run_in_executor(
                    None,
                    observed_call(cpu_model.encode, cpu_observation),
                    texts,
                    cpu_bs,
                    False,
                    None,
                )
                return await wait_for_offload(
                    cpu_future,
                    cpu_timeout,
                    cpu_observation,
                )

            def _observed_elapsed(mode: str, wall_sec: float) -> float:
                """线程路径用设备内耗时；子进程路径没有线程内观测，保留墙钟。"""
                if mode == "thread":
                    device_elapsed = cpu_observation.device_elapsed_sec
                    if device_elapsed is not None:
                        return device_elapsed
                return wall_sec

            async def _cpu_encode_process() -> np.ndarray:
                return await _run_in_encode_process(
                    texts=texts,
                    batch_size=cpu_bs,
                    device="cpu",
                    timeout=cpu_timeout,
                )

            def _emit_cpu_ok(mode: str, elapsed: float, is_primary: bool) -> None:
                stage_label = "主路径" if is_primary else "兜底"
                # cpu_main_path 主路径降为 DEBUG (部署层意图, 业务无需感知); 其余 INFO
                if cpu_main_path and is_primary:
                    logger.debug(
                        f"[safe_encode_batch] CPU {stage_label}完成: "
                        f"batch={batch_no}/{batch_count}, bs={cpu_bs}, "
                        f"elapsed_ms={int(elapsed * 1000)}, "
                        f"queue_wait_ms={meta['cpu_queue_wait_ms']}, mode={mode}, "
                        f"timeout={cpu_timeout:.1f}s, capacity={get_cpu_encode_capacity()}"
                    )
                else:
                    logger.info(
                        f"[safe_encode_batch] CPU {stage_label}完成: "
                        f"batch={batch_no}/{batch_count}, bs={cpu_bs}, "
                        f"elapsed_ms={int(elapsed * 1000)}, "
                        f"queue_wait_ms={meta['cpu_queue_wait_ms']}, mode={mode}, "
                        f"timeout={cpu_timeout:.1f}s, capacity={get_cpu_encode_capacity()}"
                    )
                vector_fallback_total.labels(flow="vectorize", reason=reason_str, mode=mode).inc()
                vector_encode_batch_total.labels(flow="vectorize", device="cpu", result="ok").inc()
                vector_encode_batch_chars.labels(flow="vectorize", device="cpu").observe(total_chars_cpu)
                vector_encode_latency_seconds.labels(flow="vectorize", device="cpu", mode=mode).observe(elapsed)
                # CpuEncodeMetric 仅刻画主进程 CPU(线程)延迟, 驱动 CPU semaphore AIMD;
                # 子进程是独立资源, 不参与主进程 CPU 并发自适应。
                if mode == "thread":
                    _record_cpu_metric(CpuEncodeMetric(
                        latency_ms=elapsed * 1000.0,
                        queue_wait_ms=queue_wait_sec * 1000.0,
                        batch_size=cpu_bs,
                        total_chars=total_chars_cpu,
                        success=True,
                    ))

            if prefer_subprocess:
                primary_mode, primary_fn = "process", _cpu_encode_process
                fallback_mode, fallback_fn = "thread", _cpu_encode_thread
            else:
                primary_mode, primary_fn = "thread", _cpu_encode_thread
                fallback_mode, fallback_fn = "process", _cpu_encode_process

            # ── 主路径 ──
            meta["recovery_mode"] = primary_mode
            t0 = time.perf_counter()
            try:
                result = await primary_fn()
                _emit_cpu_ok(
                    primary_mode,
                    _observed_elapsed(primary_mode, time.perf_counter() - t0),
                    is_primary=True,
                )
                return result, meta
            except (asyncio.TimeoutError, Exception) as primary_err:
                elapsed = _observed_elapsed(primary_mode, time.perf_counter() - t0)
                is_timeout = isinstance(primary_err, asyncio.TimeoutError)
                # 透出异常类型与消息, 否则无法区分"超时 vs 真异常"及其根因
                err_detail = (
                    "" if is_timeout
                    else f", err={type(primary_err).__name__}: {primary_err}"
                )
                logger.warning(
                    f"[safe_encode_batch] CPU {primary_mode} 主路径"
                    f"{'超时' if is_timeout else '异常'}: "
                    f"batch={batch_no}/{batch_count}, elapsed_ms={int(elapsed * 1000)}"
                    f"{err_detail}, 回退 {fallback_mode}..."
                )
                if not is_timeout:
                    logger.opt(exception=primary_err).debug(
                        "[safe_encode_batch] CPU 主路径异常堆栈 (诊断用)"
                    )
                if primary_mode == "thread":
                    _record_cpu_metric(CpuEncodeMetric(
                        latency_ms=elapsed * 1000.0,
                        queue_wait_ms=queue_wait_sec * 1000.0,
                        batch_size=cpu_bs,
                        total_chars=total_chars_cpu,
                        success=False,
                    ))

            # ── 兜底路径 (主路径失败后) ──
            meta["recovery_mode"] = fallback_mode
            t0 = time.perf_counter()
            try:
                result = await fallback_fn()
                _emit_cpu_ok(
                    fallback_mode,
                    _observed_elapsed(fallback_mode, time.perf_counter() - t0),
                    is_primary=False,
                )
                return result, meta
            except asyncio.TimeoutError:
                logger.error(
                    f"[safe_encode_batch] CPU {fallback_mode} 兜底也超时 ({cpu_timeout:.0f}s): "
                    f"batch={batch_no}/{batch_count}"
                )
                raise
            except Exception:
                logger.error(
                    f"[safe_encode_batch] CPU {fallback_mode} 兜底也失败: "
                    f"batch={batch_no}/{batch_count}",
                    exc_info=True,
                )
                raise
        finally:
            try:
                vector_cpu_inflight_gauge.dec()
            except Exception:  # noqa: BLE001
                pass
            _dec_active_encode()
