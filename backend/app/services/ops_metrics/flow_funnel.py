"""ops_metrics.flow_funnel: /flow-funnel/{flow} endpoint 业务装配 (R2-8 拆分产物).

由 admin_ops.py get_flow_funnel 内 SQL 装配 funnel + outcome_distribution + time_series 迁出。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_observation import TaskObservation, TaskPhase


async def compute_flow_funnel(
    *,
    db: AsyncSession,
    flow: str,
    cs: datetime,
    now: datetime,
    window: str,
) -> dict[str, Any]:
    """三业务子页阶段漏斗数据.

    flow ∈ {tender, collusion, writer, process}
    基于 task_observations 表聚合: 漏斗 / outcome 分布 / 时间序列。
    返回 ApiResponse.data 字典,7 个顶层 key 与原 endpoint byte-equal。
    """
    rows_start = (await db.execute(
        select(TaskObservation.task_type, func.count(TaskObservation.id))
        .where(TaskObservation.flow == flow)
        .where(TaskObservation.phase == TaskPhase.START)
        .where(TaskObservation.created_at >= cs)
        .group_by(TaskObservation.task_type)
    )).all()
    rows_end = (await db.execute(
        select(
            TaskObservation.task_type,
            TaskObservation.outcome,
            func.count(TaskObservation.id),
            func.avg(TaskObservation.duration_ms),
        )
        .where(TaskObservation.flow == flow)
        .where(TaskObservation.phase == TaskPhase.END)
        .where(TaskObservation.created_at >= cs)
        .group_by(TaskObservation.task_type, TaskObservation.outcome)
    )).all()

    start_by_type: dict[str, int] = {t: c for t, c in rows_start}
    end_by_type: dict[str, dict[str, Any]] = {}
    for tt, oc, cnt, avg_ms in rows_end:
        oc_val = oc.value if hasattr(oc, "value") else (oc or "unknown")
        bucket = end_by_type.setdefault(tt, {
            "total": 0,
            "by_outcome": {},
            "avg_duration_ms": 0.0,
            "_weighted_sum": 0.0,
        })
        bucket["total"] += cnt
        bucket["by_outcome"][oc_val] = cnt
        bucket["_weighted_sum"] += float(avg_ms or 0) * cnt
    for tt, bucket in end_by_type.items():
        if bucket["total"] > 0:
            bucket["avg_duration_ms"] = round(bucket["_weighted_sum"] / bucket["total"], 2)
        del bucket["_weighted_sum"]

    task_types = sorted(set(start_by_type.keys()) | set(end_by_type.keys()))

    funnel: list[dict[str, Any]] = []
    for tt in task_types:
        started = start_by_type.get(tt, 0)
        ended_bucket = end_by_type.get(tt, {"total": 0, "by_outcome": {}, "avg_duration_ms": 0.0})
        success = ended_bucket["by_outcome"].get("success", 0)
        failure = ended_bucket["by_outcome"].get("failure", 0)
        other = ended_bucket["total"] - success - failure
        funnel.append({
            "task_type": tt,
            "started": started,
            "ended": ended_bucket["total"],
            "success": success,
            "failure": failure,
            "other": max(other, 0),
            "success_ratio": round(success / ended_bucket["total"], 4) if ended_bucket["total"] else 0.0,
            "avg_duration_ms": ended_bucket["avg_duration_ms"],
        })

    outcome_rows = (await db.execute(
        select(TaskObservation.outcome, func.count(TaskObservation.id))
        .where(TaskObservation.flow == flow)
        .where(TaskObservation.phase == TaskPhase.END)
        .where(TaskObservation.created_at >= cs)
        .group_by(TaskObservation.outcome)
    )).all()
    outcome_distribution: dict[str, int] = {}
    for oc, cnt in outcome_rows:
        key = oc.value if hasattr(oc, "value") else (oc or "unknown")
        outcome_distribution[key] = cnt

    series_rows = (await db.execute(
        select(
            func.date_trunc("hour", TaskObservation.created_at).label("bucket"),
            TaskObservation.outcome,
            func.count(TaskObservation.id),
        )
        .where(TaskObservation.flow == flow)
        .where(TaskObservation.phase == TaskPhase.END)
        .where(TaskObservation.created_at >= cs)
        .group_by("bucket", TaskObservation.outcome)
        .order_by("bucket")
    )).all()
    series: dict[str, dict[str, int]] = {}
    for bucket, oc, cnt in series_rows:
        ts_key = bucket.isoformat() if bucket else ""
        oc_key = oc.value if hasattr(oc, "value") else (oc or "unknown")
        series.setdefault(ts_key, {}).setdefault(oc_key, 0)
        series[ts_key][oc_key] += cnt

    time_series = [
        {"ts": ts, "counts": counts, "total": sum(counts.values())}
        for ts, counts in sorted(series.items())
    ]

    return {
        "flow": flow,
        "window": window,
        "window_start": cs.isoformat(),
        "generated_at": now.isoformat(),
        "funnel": funnel,
        "outcome_distribution": outcome_distribution,
        "time_series": time_series,
    }
