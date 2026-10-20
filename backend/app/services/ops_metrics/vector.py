"""ops_metrics.vector: 向量与解析 layer (R2-8 拆分产物).

由 admin_ops.py _layer_vector 迁出,通过 thin forwarder 委托。
prom_counter_sum 通过 kwargs 依赖注入透传 (兼容 monkey patch)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vector_job_state import (
    ACTIVE_VECTOR_JOB_STATES,
    VectorJobState,
)


async def layer_vector(
    *,
    db: AsyncSession,
    cs: datetime,
    bs: datetime,
    prom_counter_sum: Callable[..., float],
) -> list[dict[str, Any]]:
    """向量与解析: fallback / 降级 / chunk 质量."""
    del bs

    total_active = (await db.execute(
        select(func.count(VectorJobState.id))
        .where(VectorJobState.status.in_([s for s in ACTIVE_VECTOR_JOB_STATES]))
    )).scalar() or 0
    degraded_active = (await db.execute(
        select(func.count(VectorJobState.id))
        .where(VectorJobState.status.in_([s for s in ACTIVE_VECTOR_JOB_STATES]))
        .where(VectorJobState.degraded_to_cpu == 1)
    )).scalar() or 0

    degrade_ratio = (degraded_active / total_active) if total_active > 0 else 0.0

    fallback_total = prom_counter_sum("vector_fallback_total")
    chunk_clean = prom_counter_sum("chunk_text_quality_total", {"outcome": "clean"})
    chunk_degraded = prom_counter_sum("chunk_text_quality_total", {"outcome": "degraded"})
    chunk_rejected = prom_counter_sum("chunk_text_quality_total", {"outcome": "rejected"})
    chunk_total = chunk_clean + chunk_degraded + chunk_rejected
    clean_ratio = chunk_clean / chunk_total if chunk_total > 0 else 0.0

    return [
        {
            "name": "vector_degrade_to_cpu_ratio",
            "layer": "vector",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "gauge",
            "description": "活跃向量任务中降级 CPU 的占比",
            "value": round(degrade_ratio, 4),
            "baseline": round(degrade_ratio, 4),
            "verdict": (
                "neutral"
                if total_active == 0
                else ("critical" if degrade_ratio > 0.3 else ("warn" if degrade_ratio > 0.1 else "stable"))
            ),
            "delta_pct": 0.0,
            "reason": (
                "当前时间范围内暂无活跃向量任务"
                if total_active == 0
                else f"degraded={degraded_active}/active={total_active}"
            ),
        },
        {
            "name": "vector_fallback_total",
            "layer": "vector",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "counter",
            "description": "向量化 fallback 累计次数",
            "value": fallback_total,
            "baseline": fallback_total,
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "累计指标",
        },
        {
            "name": "chunk_clean_ratio",
            "layer": "vector",
            "pillar": "quality",
            "direction": "higher_better",
            "kind": "gauge",
            "description": "chunk 文本质量 clean 占比",
            "value": round(clean_ratio, 4),
            "baseline": round(clean_ratio, 4),
            "verdict": (
                "neutral"
                if chunk_total == 0
                else ("stable" if clean_ratio >= 0.9 else ("warn" if clean_ratio >= 0.7 else "critical"))
            ),
            "delta_pct": 0.0,
            "reason": (
                "当前时间范围内暂无文本质量样本"
                if chunk_total == 0
                else f"clean={int(chunk_clean)}/total={int(chunk_total)}"
            ),
        },
    ]
