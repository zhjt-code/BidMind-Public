"""ops_metrics.metrics_hotspots: /metrics/hotspots endpoint 业务装配 (R2-8 拆分产物).

由 admin_ops.py get_metrics_hotspots 内 catalog × snapshot 排序 + related 装配迁出。
collect_related_events 通过 kwargs 依赖注入透传 (保留 admin_ops 内 helper)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def list_metrics_hotspots(
    *,
    db: AsyncSession,
    cs: datetime,
    now: datetime,
    window: str,
    limit: int,
    collect_related_events: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Phase 5: 优化热点定位端点.

    返回近窗口 verdict 为 warn/critical/degrading 的指标, 按"劣化幅度 x 样本点数"排序,
    附带 source / related 等信息。返回 ApiResponse.data 字典。
    """
    from app.core.observability.rule_engine import evaluate_metric
    from app.models.metric_catalog import MetricCatalog
    from app.models.metric_snapshot import MetricSnapshot

    catalog_rows = (await db.execute(select(MetricCatalog))).scalars().all()

    hotspots: list[dict[str, Any]] = []
    for row in catalog_rows:
        ts_rows = (await db.execute(
            select(MetricSnapshot)
            .where(MetricSnapshot.name == row.name)
            .where(MetricSnapshot.bucket_ts >= cs)
            .order_by(MetricSnapshot.bucket_ts)
        )).scalars().all()

        values = [t.avg for t in ts_rows]
        if not values:
            continue

        verdict = await evaluate_metric(
            db,
            metric_name=row.name,
            series=values,
            direction=row.direction,
        )
        if verdict.verdict not in {"warn", "critical", "degrading"}:
            continue

        related = await collect_related_events(
            db,
            cs=cs,
            now=now,
            name=row.name,
        )

        hotspots.append({
            "name": row.name,
            "layer": row.layer,
            "pillar": row.pillar,
            "business": row.business,
            "direction": row.direction,
            "kind": row.kind,
            "description": row.description or "",
            "verdict": verdict.verdict,
            "algorithm": verdict.algorithm,
            "reason": verdict.reason,
            "score": verdict.score,
            "change_points": verdict.change_points,
            "detail": verdict.detail,
            "samples": len(values),
            "source": {
                "module": row.source_module or "",
                "qualname": row.source_qualname or "",
                "file": row.source_file or "",
                "line": row.source_line or 0,
            },
            "related": related,
            "_rank": abs(verdict.score) * (1.0 + len(values) / 100.0),
        })

    hotspots.sort(key=lambda x: x["_rank"], reverse=True)
    hotspots = hotspots[:limit]
    for h in hotspots:
        h.pop("_rank", None)

    return {
        "window": window,
        "generated_at": now.isoformat(),
        "total": len(hotspots),
        "items": hotspots,
    }
