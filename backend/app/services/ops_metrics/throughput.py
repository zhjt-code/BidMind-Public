"""ops_metrics.throughput: 任务吞吐 layer (R2-8 拆分产物).

由 admin_ops.py _layer_throughput 迁出,通过 thin forwarder 委托。
verdict_from_ratio 通过 kwargs 依赖注入透传 (兼容 monkey patch)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vector_job_state import (
    ACTIVE_VECTOR_JOB_STATES,
    VectorJobState,
    VectorJobStatus,
)


async def layer_throughput(
    *,
    db: AsyncSession,
    cs: datetime,
    bs: datetime,
    verdict_from_ratio: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    """任务吞吐: vector_job_states 状态分布 + 审计日志中 task 相关动作数."""
    rows = (await db.execute(
        select(VectorJobState.status, func.count(VectorJobState.id))
        .group_by(VectorJobState.status)
    )).all()

    status_counts: dict[str, int] = {s.value: 0 for s in VectorJobStatus}
    for st, cnt in rows:
        key = st.value if hasattr(st, "value") else str(st)
        status_counts[key] = cnt

    active = sum(status_counts[s.value] for s in ACTIVE_VECTOR_JOB_STATES)
    lost = status_counts.get(VectorJobStatus.LOST.value, 0)

    completed_cur = (await db.execute(
        select(func.count(VectorJobState.id))
        .where(VectorJobState.status == VectorJobStatus.COMPLETED)
        .where(VectorJobState.finished_at >= cs)
    )).scalar() or 0

    completed_base = (await db.execute(
        select(func.count(VectorJobState.id))
        .where(VectorJobState.status == VectorJobStatus.COMPLETED)
        .where(VectorJobState.finished_at >= bs)
        .where(VectorJobState.finished_at < cs)
    )).scalar() or 0

    failed_cur = (await db.execute(
        select(func.count(VectorJobState.id))
        .where(VectorJobState.status == VectorJobStatus.FAILED)
        .where(VectorJobState.finished_at >= cs)
    )).scalar() or 0

    failed_base = (await db.execute(
        select(func.count(VectorJobState.id))
        .where(VectorJobState.status == VectorJobStatus.FAILED)
        .where(VectorJobState.finished_at >= bs)
        .where(VectorJobState.finished_at < cs)
    )).scalar() or 0

    queue_wait_avg = (await db.execute(
        select(func.avg(
            func.extract("epoch", VectorJobState.claimed_at) - func.extract("epoch", VectorJobState.enqueued_at)
        ))
        .where(VectorJobState.enqueued_at.isnot(None))
        .where(VectorJobState.claimed_at.isnot(None))
        .where(VectorJobState.enqueued_at >= cs)
    )).scalar() or 0.0
    queue_wait_count = (await db.execute(
        select(func.count(VectorJobState.id))
        .where(VectorJobState.enqueued_at.isnot(None))
        .where(VectorJobState.claimed_at.isnot(None))
        .where(VectorJobState.enqueued_at >= cs)
    )).scalar() or 0

    return [
        {
            "name": "task_active_gauge",
            "layer": "throughput",
            "pillar": "reliability",
            "direction": "neutral",
            "kind": "gauge",
            "description": "当前活跃 vector_job 数 (pending/queued/claimed/running/lost/recovering)",
            "value": float(active),
            "baseline": float(active),
            "verdict": "warn" if lost > 0 else "stable",
            "delta_pct": 0.0,
            "reason": f"active={active} lost={lost}",
            "breakdown": status_counts,
        },
        {
            "name": "task_completed_total",
            "layer": "throughput",
            "pillar": "efficiency",
            "direction": "higher_better",
            "kind": "counter",
            "description": "近窗口完成的 vector_job 数",
            "value": float(completed_cur),
            "baseline": float(completed_base),
            **verdict_from_ratio(completed_cur, completed_base, "higher_better"),
        },
        {
            "name": "task_failed_total",
            "layer": "throughput",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "counter",
            "description": "近窗口失败的 vector_job 数",
            "value": float(failed_cur),
            "baseline": float(failed_base),
            **verdict_from_ratio(failed_cur, failed_base, "lower_better"),
        },
        {
            "name": "task_queue_wait_seconds_avg",
            "layer": "throughput",
            "pillar": "efficiency",
            "direction": "lower_better",
            "kind": "histogram",
            "description": "近窗口任务排队等待平均耗时 (秒, enqueued->claimed)",
            "value": float(queue_wait_avg),
            "baseline": float(queue_wait_avg),
            "verdict": "neutral" if queue_wait_count == 0 else "stable",
            "delta_pct": 0.0,
            "reason": "当前时间范围内暂无排队等待样本" if queue_wait_count == 0 else f"avg={round(queue_wait_avg, 2)}s",
        },
    ]
