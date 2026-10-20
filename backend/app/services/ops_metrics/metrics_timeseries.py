"""ops_metrics.metrics_timeseries: /metrics/timeseries endpoint 业务装配 (R2-8 拆分产物).

由 admin_ops.py get_metrics_timeseries 内 SQL + 点装配迁出。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_metrics_timeseries(
    *,
    db: AsyncSession,
    cs: datetime,
    now: datetime,
    window: str,
    name: str,
    labels_hash: Optional[str],
) -> dict[str, Any]:
    """单指标时间序列 (从 metric_snapshots 温层读).

    返回 ApiResponse.data 字典: {name, window, generated_at, points, total}。
    """
    from app.models.metric_snapshot import MetricSnapshot

    stmt = (
        select(MetricSnapshot)
        .where(MetricSnapshot.name == name)
        .where(MetricSnapshot.bucket_ts >= cs)
        .order_by(MetricSnapshot.bucket_ts)
    )
    if labels_hash:
        stmt = stmt.where(MetricSnapshot.labels_hash == labels_hash)
    rows = (await db.execute(stmt)).scalars().all()

    points = [
        {
            "ts": r.bucket_ts.isoformat(),
            "bucket_seconds": r.bucket_seconds,
            "count": r.count,
            "sum": r.sum,
            "avg": r.avg,
            "p50": r.p50,
            "p95": r.p95,
        }
        for r in rows
    ]

    return {
        "name": name,
        "window": window,
        "generated_at": now.isoformat(),
        "points": points,
        "total": len(points),
    }
