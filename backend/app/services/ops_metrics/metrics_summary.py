"""ops_metrics.metrics_summary: /metrics/summary endpoint 业务装配 (R2-8 拆分产物).

由 admin_ops.py get_metrics_summary 内 catalog × snapshot 聚合 + verdict 调用迁出。
baseline_data 由 endpoint 调用 _load_baseline_json 后注入 (避免子模块依赖 __file__ 路径)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def list_metrics_summary(
    *,
    db: AsyncSession,
    cs: datetime,
    now: datetime,
    window: str,
    layer: Optional[str],
    baseline: Optional[str],
    baseline_data: dict[str, Any],
) -> dict[str, Any]:
    """返回每条指标当前值 + Phase 4 MetricRule 驱动的 verdict.

    返回 ApiResponse.data 字典: {window, baseline, generated_at, metrics}。
    """
    from app.core.observability.rule_engine import evaluate_metric
    from app.models.metric_catalog import MetricCatalog
    from app.models.metric_snapshot import MetricSnapshot

    stmt = select(MetricCatalog)
    if layer:
        stmt = stmt.where(MetricCatalog.layer == layer)
    catalog_rows = (await db.execute(stmt)).scalars().all()

    result: list[dict[str, Any]] = []
    for row in catalog_rows:
        ts_stmt = (
            select(MetricSnapshot)
            .where(MetricSnapshot.name == row.name)
            .where(MetricSnapshot.bucket_ts >= cs)
            .order_by(MetricSnapshot.bucket_ts)
        )
        ts_rows = (await db.execute(ts_stmt)).scalars().all()

        series_points = [
            {
                "ts": t.bucket_ts.isoformat(),
                "count": t.count,
                "sum": t.sum,
                "avg": t.avg,
                "p95": t.p95,
            }
            for t in ts_rows
        ]
        series_values: list[float] = [s["avg"] for s in series_points]
        cur_avg = sum(series_values) / len(series_values) if series_values else 0.0

        verdict = await evaluate_metric(
            db,
            metric_name=row.name,
            series=series_values,
            direction=row.direction,
        )

        vs_baseline: Optional[dict[str, Any]] = None
        if baseline_data and row.name in baseline_data:
            b_value = baseline_data[row.name]
            if isinstance(b_value, (int, float)) and b_value != 0:
                delta_pct = round((cur_avg - b_value) / abs(b_value) * 100, 2)
            else:
                delta_pct = 0.0
            vs_baseline = {
                "baseline_value": b_value,
                "current_value": round(cur_avg, 4),
                "delta_pct": delta_pct,
                "source": f"docs/baselines/{baseline}.json",
            }

        result.append({
            "name": row.name,
            "layer": row.layer,
            "pillar": row.pillar,
            "business": row.business,
            "direction": row.direction,
            "kind": row.kind,
            "description": row.description or "",
            "value": round(cur_avg, 4),
            "series_points": len(series_values),
            "verdict": verdict.verdict,
            "algorithm": verdict.algorithm,
            "reason": verdict.reason,
            "score": verdict.score,
            "change_points": verdict.change_points,
            "detail": verdict.detail,
            "vs_baseline": vs_baseline,
        })

    return {
        "window": window,
        "baseline": baseline or "none",
        "generated_at": now.isoformat(),
        "metrics": result,
    }
