"""GPU AIMD watermark guard.

职责: 判断一次 embedding AIMD 升档候选 batch_size 是否会突破 GPU 显存水位线。
不做: 不执行 encode、不获取 GPU permit、不修改 AIMD 状态、不触发 CPU fallback。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from app.core.config import settings
from app.core.gpu.admission import estimate_embedding_required_mb, get_gpu_snapshot


@dataclass(frozen=True)
class GpuWatermarkDecision:
    """GPU AIMD 升档水位判断结果。"""

    allow_increase: bool
    reason: str
    max_used_ratio: float
    projected_used_ratio: Optional[float]
    snapshot_used_ratio: Optional[float]
    estimated_required_mb: Optional[int]
    free_mb: Optional[int]
    total_mb: Optional[int]

    def to_dict(self) -> dict:
        """转为可写入日志/heartbeat meta 的普通字典。"""

        return asdict(self)


def _clamp_max_used_ratio(raw: float) -> float:
    """Clamp configured watermark ratio to a safe operational range."""

    return min(0.98, max(0.70, float(raw)))


def _missing_snapshot_allows_increase() -> bool:
    """Return how AIMD should behave when GPU snapshot is unavailable."""

    policy = str(
        getattr(settings, "VECTOR_GPU_AIMD_WATERMARK_ON_SNAPSHOT_MISSING", "allow")
        or "allow"
    ).strip().lower()
    return policy not in {"block", "deny", "pause"}


def _texts_for_candidate_estimate(
    *,
    texts: list[str],
    candidate_batch_size: int,
) -> list[str]:
    """Build a conservative text sample for candidate batch-size estimation."""

    effective_bs = max(1, int(candidate_batch_size or 1))
    if not texts or len(texts) >= effective_bs:
        return texts
    longest_text = max(texts, key=lambda item: len(item or ""))
    return [*texts, *([longest_text] * (effective_bs - len(texts)))]


def should_allow_aimd_increase(
    *,
    texts: list[str],
    candidate_batch_size: int,
) -> GpuWatermarkDecision:
    """判断候选升档是否会突破 GPU 显存水位线。"""

    raw_ratio = float(getattr(settings, "VECTOR_GPU_AIMD_MAX_USED_RATIO", 0.90))
    max_used_ratio = _clamp_max_used_ratio(raw_ratio)

    if not bool(getattr(settings, "VECTOR_GPU_AIMD_WATERMARK_ENABLED", True)):
        return GpuWatermarkDecision(
            allow_increase=True,
            reason="disabled",
            max_used_ratio=max_used_ratio,
            projected_used_ratio=None,
            snapshot_used_ratio=None,
            estimated_required_mb=None,
            free_mb=None,
            total_mb=None,
        )

    snapshot = get_gpu_snapshot()
    if snapshot is None:
        allow = _missing_snapshot_allows_increase()
        return GpuWatermarkDecision(
            allow_increase=allow,
            reason="snapshot_missing_allow" if allow else "snapshot_missing_block",
            max_used_ratio=max_used_ratio,
            projected_used_ratio=None,
            snapshot_used_ratio=None,
            estimated_required_mb=None,
            free_mb=None,
            total_mb=None,
        )

    if snapshot.total_mb <= 0:
        return GpuWatermarkDecision(
            allow_increase=True,
            reason="invalid_snapshot_allow",
            max_used_ratio=max_used_ratio,
            projected_used_ratio=None,
            snapshot_used_ratio=None,
            estimated_required_mb=None,
            free_mb=snapshot.free_mb,
            total_mb=snapshot.total_mb,
        )

    estimate_texts = _texts_for_candidate_estimate(
        texts=texts,
        candidate_batch_size=candidate_batch_size,
    )
    estimated_required_mb = estimate_embedding_required_mb(
        estimate_texts,
        max(1, int(candidate_batch_size or 1)),
    )
    snapshot_used_ratio = snapshot.used_mb / snapshot.total_mb
    projected_used_ratio = (
        snapshot.used_mb + estimated_required_mb
    ) / snapshot.total_mb
    allow_increase = projected_used_ratio <= max_used_ratio

    return GpuWatermarkDecision(
        allow_increase=allow_increase,
        reason="under_watermark" if allow_increase else "watermark_exceeded",
        max_used_ratio=max_used_ratio,
        projected_used_ratio=projected_used_ratio,
        snapshot_used_ratio=snapshot_used_ratio,
        estimated_required_mb=estimated_required_mb,
        free_mb=snapshot.free_mb,
        total_mb=snapshot.total_mb,
    )
