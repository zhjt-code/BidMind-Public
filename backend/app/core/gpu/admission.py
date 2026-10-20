"""GPU Admission Control.

This module decides whether a CUDA critical section may enter GPU execution.
It uses NVML as the primary source of GPU memory facts and Redis resource
permits to make the decision visible across worker processes.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Literal, Optional

from loguru import logger

from app.core.config import settings


GpuAdmissionAction = Literal["use_gpu", "cpu_fallback"]

# 持有中的 GPU 许可心跳, 按 owner_key 索引; 释放时先停心跳再删 owner。
_heartbeats: dict[str, asyncio.Task] = {}


@dataclass(frozen=True)
class GpuSnapshot:
    """GPU runtime state used by admission decisions."""

    total_mb: int
    used_mb: int
    free_mb: int
    utilization: int
    source: str


@dataclass(frozen=True)
class GpuAdmissionDecision:
    """Decision returned before entering a CUDA critical section."""

    action: GpuAdmissionAction
    reason: str
    estimated_required_mb: int
    min_free_mb: int = 0
    required_free_mb: int = 0
    snapshot: Optional[GpuSnapshot] = None
    acquired_permit: bool = False


def estimate_embedding_required_mb(texts: list[str], batch_size: int) -> int:
    """Conservatively estimate embedding GPU memory pressure in MB.

    ``EmbeddingModel.encode`` forwards ``batch_size`` to the underlying model,
    so the peak activation/workspace pressure is closer to the largest internal
    micro-batch than to the whole outer ``texts`` list.
    """

    effective_bs = max(1, int(batch_size or 1))
    max_batch_chars = 0
    for start in range(0, len(texts), effective_bs):
        batch_chars = sum(len(text or "") for text in texts[start:start + effective_bs])
        max_batch_chars = max(max_batch_chars, batch_chars)

    approx_tokens = max(1, max_batch_chars // 2)
    base_mb = int(getattr(settings, "GPU_ADMISSION_ESTIMATE_BASE_MB", 512))
    per_1k_mb = int(getattr(settings, "GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB", 128))
    token_mb = ((approx_tokens + 999) // 1000) * per_1k_mb
    return int(base_mb + token_mb)


def _snapshot_from_nvml() -> Optional[GpuSnapshot]:
    """Read GPU memory through NVIDIA's NVML Python bindings."""

    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return GpuSnapshot(
                total_mb=int(memory.total // (1024 * 1024)),
                used_mb=int(memory.used // (1024 * 1024)),
                free_mb=int(memory.free // (1024 * 1024)),
                utilization=int(getattr(utilization, "gpu", 0)),
                source="nvml",
            )
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[GPUAdmission] NVML snapshot unavailable: {exc}")
        return None


def _snapshot_from_nvidia_smi() -> Optional[GpuSnapshot]:
    """Development fallback when NVML bindings are unavailable."""

    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        first_line = completed.stdout.strip().splitlines()[0]
        total, used, free, utilization = [
            int(part.strip()) for part in first_line.split(",")
        ]
        return GpuSnapshot(
            total_mb=total,
            used_mb=used,
            free_mb=free,
            utilization=utilization,
            source="nvidia-smi",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[GPUAdmission] nvidia-smi snapshot unavailable: {exc}")
        return None


def get_gpu_snapshot() -> Optional[GpuSnapshot]:
    """Return the current GPU snapshot using NVML first."""

    snapshot = _snapshot_from_nvml()
    if snapshot is not None:
        return snapshot
    if bool(getattr(settings, "GPU_ADMISSION_NVML_REQUIRED", False)):
        return None
    return _snapshot_from_nvidia_smi()


def vram_allows_gpu_retry(estimated_required_mb: int) -> bool:
    """职责: 只读显存水位判定是否值得重试 GPU (粘滞自愈探查用)。
    不做: 不抢许可、不写指标、不改变冷却状态、不发起 encode。

    返回 True 表示当前 snapshot.free_mb 足够支撑一次 embedding 调用,
    可作为 encode_gate 粘滞复检的零成本预检依据;
    返回 False (含 snapshot 不可得) 表示继续走 CPU 路径。
    """
    snapshot = get_gpu_snapshot()
    if snapshot is None:
        return False
    min_free_mb = int(getattr(settings, "GPU_ADMISSION_MIN_FREE_MB", 1024))
    required_free = max(1, int(estimated_required_mb)) + min_free_mb
    return int(snapshot.free_mb) >= required_free


def _device_idle_with_capacity(snapshot: GpuSnapshot, required_free_mb: int) -> bool:
    """职责: 判断快照是否说明设备空闲且剩余显存够本次进入。
    不做: 不读 Redis, 不改许可, 不发起 encode。
    """

    if not bool(getattr(settings, "GPU_ADMISSION_IDLE_RECLAIM_ENABLED", True)):
        return False
    idle_max = int(getattr(settings, "GPU_ADMISSION_IDLE_UTIL_MAX", 10))
    return (
        int(snapshot.utilization) <= idle_max
        and int(snapshot.free_mb) >= int(required_free_mb)
    )


def _record_decision(decision: GpuAdmissionDecision, kind: str) -> None:
    """Best-effort Prometheus metric updates."""

    try:
        from app.core.observability.metrics import (
            gpu_admission_decision_total,
            gpu_memory_free_mb,
        )

        gpu_admission_decision_total.labels(
            kind=kind,
            action=decision.action,
            reason=decision.reason,
        ).inc()
        if decision.snapshot is not None:
            gpu_memory_free_mb.labels(source=decision.snapshot.source).set(
                decision.snapshot.free_mb
            )
        _record_inflight(kind, 1 if decision.acquired_permit else 0)
    except Exception:
        pass


def _record_inflight(kind: str, value: int) -> None:
    """Best-effort update for current GPU permit occupancy."""

    try:
        from app.core.observability.metrics import gpu_inflight_gauge

        gpu_inflight_gauge.labels(kind=kind).set(value)
    except Exception:
        pass


async def _acquire_gpu_permit(
    kind: str,
    owner: str,
    *,
    wait_timeout: Optional[float] = None,
) -> tuple[object, str, bool]:
    """Acquire the cross-process GPU permit from Redis and keep it held."""

    try:
        from app.core.redis import get_redis_pool
        from app.core.tasks.resource_permit import (
            acquire_resource_permit,
            start_owner_heartbeat,
        )

        redis = await get_redis_pool()
        limit = int(getattr(settings, "GPU_ADMISSION_PERMIT_LIMIT", 1))
        if wait_timeout is None:
            wait_timeout = float(getattr(settings, "GPU_ADMISSION_WAIT_TIMEOUT_SEC", 0.5))
        owner_key = f"{kind}:{owner}"
        acquired = await acquire_resource_permit(
            redis,
            "gpu",
            limit=limit,
            wait_timeout=wait_timeout,
            owner=owner_key,
        )
        if acquired:
            heartbeat = start_owner_heartbeat(redis, "gpu", owner_key)
            if heartbeat is not None:
                _heartbeats[owner_key] = heartbeat
        return redis, owner_key, bool(acquired)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[GPUAdmission] GPU permit unavailable: kind={kind}, err={exc}")
        return None, "", False


async def _release_gpu_permit(redis: object, owner: str) -> None:
    """Release a held GPU permit."""

    if redis is None:
        return
    try:
        from app.core.tasks.resource_permit import (
            release_resource_owner,
            release_resource_permit,
            stop_owner_heartbeat,
        )

        await stop_owner_heartbeat(_heartbeats.pop(owner, None))
        await release_resource_owner(redis, "gpu", owner)
        await release_resource_permit(redis, "gpu")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[GPUAdmission] release failed: owner={owner}, err={exc}")


@asynccontextmanager
async def gpu_admission_context(
    *,
    kind: str,
    estimated_required_mb: int,
    owner: str,
    allow_wait: bool = False,
    resident_model_loaded: bool = False,
) -> AsyncIterator[GpuAdmissionDecision]:
    """Yield a decision and hold the GPU permit while CUDA work runs."""

    if not bool(getattr(settings, "GPU_ADMISSION_ENABLED", True)):
        decision = GpuAdmissionDecision(
            action="use_gpu",
            reason="disabled",
            estimated_required_mb=estimated_required_mb,
        )
        _record_decision(decision, kind)
        yield decision
        return

    snapshot = get_gpu_snapshot()
    if snapshot is None:
        decision = GpuAdmissionDecision(
            action="cpu_fallback",
            reason="snapshot_unavailable",
            estimated_required_mb=estimated_required_mb,
        )
        _record_decision(decision, kind)
        yield decision
        return

    min_free_mb = int(getattr(settings, "GPU_ADMISSION_MIN_FREE_MB", 1024))
    required_free = estimated_required_mb + min_free_mb
    if snapshot.free_mb < required_free:
        if resident_model_loaded:
            redis, permit_owner, acquired = await _acquire_gpu_permit(kind, owner)
            if acquired:
                decision = GpuAdmissionDecision(
                    action="use_gpu",
                    reason="resident_model_loaded",
                    snapshot=snapshot,
                    estimated_required_mb=estimated_required_mb,
                    min_free_mb=min_free_mb,
                    required_free_mb=required_free,
                    acquired_permit=True,
                )
                _record_decision(decision, kind)
                started_at = time.monotonic()
                try:
                    yield decision
                finally:
                    await _release_gpu_permit(redis, permit_owner)
                    _record_inflight(kind, 0)
                    elapsed_ms = int((time.monotonic() - started_at) * 1000)
                    logger.debug(
                        f"[GPUAdmission] released: kind={kind}, owner={owner}, "
                        f"elapsed_ms={elapsed_ms}, reason=resident_model_loaded"
                    )
                return

            decision = GpuAdmissionDecision(
                action="cpu_fallback",
                reason="inflight_full",
                snapshot=snapshot,
                estimated_required_mb=estimated_required_mb,
                min_free_mb=min_free_mb,
                required_free_mb=required_free,
            )
            _record_decision(decision, kind)
            yield decision
            return

        decision = GpuAdmissionDecision(
            action="cpu_fallback",
            reason="insufficient_vram",
            snapshot=snapshot,
            estimated_required_mb=estimated_required_mb,
            min_free_mb=min_free_mb,
            required_free_mb=required_free,
        )
        _record_decision(decision, kind)
        yield decision
        return

    device_idle = _device_idle_with_capacity(snapshot, required_free)
    redis, permit_owner, acquired = await _acquire_gpu_permit(
        kind,
        owner,
        # 设备已经空闲时, 占满的许可不会在等待窗内自己消失。
        wait_timeout=0.0 if device_idle else None,
    )
    if not acquired and allow_wait:
        # Small grace window for callers that explicitly prefer waiting over CPU fallback.
        # ponytail: this retry restarts the permit wait instead of sharing one deadline, so
        # the ceiling is 3x GPU_ADMISSION_WAIT_TIMEOUT_SEC. Upgrade path: pass a single
        # deadline down to _acquire_gpu_permit. No caller sets allow_wait today.
        await asyncio.sleep(float(getattr(settings, "GPU_ADMISSION_WAIT_TIMEOUT_SEC", 0.5)))
        redis, permit_owner, acquired = await _acquire_gpu_permit(kind, owner)

    if not acquired and device_idle:
        # ponytail: 用进入时的一次利用率采样。空闲且显存够仍占满, 视为许可
        # 没有对应设备占用, 立即回收。升级路径: 等待循环内连续采样, 避免
        # 内核间隙的单次低利用率触发回收。
        from app.core.tasks.resource_permit import clear_resource_occupancy

        if await clear_resource_occupancy(redis, "gpu"):
            logger.info(
                f"[GPUAdmission] 设备空闲但许可占满, 回收后重试: kind={kind}, "
                f"free_mb={snapshot.free_mb}, util={snapshot.utilization}, "
                f"required_free_mb={required_free}"
            )
            redis, permit_owner, acquired = await _acquire_gpu_permit(
                kind, owner, wait_timeout=0.0,
            )

    if not acquired:
        decision = GpuAdmissionDecision(
            action="cpu_fallback",
            reason="inflight_full",
            snapshot=snapshot,
            estimated_required_mb=estimated_required_mb,
            min_free_mb=min_free_mb,
            required_free_mb=required_free,
        )
        _record_decision(decision, kind)
        yield decision
        return

    decision = GpuAdmissionDecision(
        action="use_gpu",
        reason="admitted",
        snapshot=snapshot,
        estimated_required_mb=estimated_required_mb,
        min_free_mb=min_free_mb,
        required_free_mb=required_free,
        acquired_permit=True,
    )
    _record_decision(decision, kind)
    started_at = time.monotonic()
    try:
        yield decision
    finally:
        await _release_gpu_permit(redis, permit_owner)
        _record_inflight(kind, 0)
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.debug(
            f"[GPUAdmission] released: kind={kind}, owner={owner}, elapsed_ms={elapsed_ms}"
        )
