"""ops_metrics.metrics_catalog: /metrics/catalog endpoint 业务装配 (R2-8 拆分产物).

由 admin_ops.py get_metrics_catalog 内 SQL + registry fallback 迁出。
safe_json_list / safe_json_dict 通过 kwargs 依赖注入 (保留 admin_ops 内 helper 作为
未来 patch 锚点)。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def list_metrics_catalog(
    *,
    db: AsyncSession,
    layer: Optional[str],
    pillar: Optional[str],
    business: Optional[str],
    safe_json_list: Callable[[Optional[str]], list],
    safe_json_dict: Callable[[Optional[str]], dict],
) -> dict[str, Any]:
    """返回 metric_catalog 表 (启动期扫描的指标目录).

    若 DB 中尚未持久化 (首次启动), 回退读取内存 MetricsRegistry。
    返回 ApiResponse.data 字典: {items, total}。
    """
    from app.core.observability.registry import get_metrics_registry
    from app.models.metric_catalog import MetricCatalog

    items: list[dict[str, Any]] = []
    try:
        stmt = select(MetricCatalog)
        if layer:
            stmt = stmt.where(MetricCatalog.layer == layer)
        if pillar:
            stmt = stmt.where(MetricCatalog.pillar == pillar)
        if business:
            stmt = stmt.where(MetricCatalog.business == business)
        rows = (await db.execute(stmt)).scalars().all()
        for row in rows:
            items.append({
                "name": row.name,
                "layer": row.layer,
                "pillar": row.pillar,
                "business": row.business,
                "direction": row.direction,
                "kind": row.kind,
                "description": row.description or "",
                "labels": safe_json_list(row.labels_json),
                "thresholds": safe_json_dict(row.thresholds_json),
                "source": {
                    "module": row.source_module or "",
                    "qualname": row.source_qualname or "",
                    "file": row.source_file or "",
                    "line": row.source_line or 0,
                },
                "unit": row.unit,
            })
    except Exception:
        items = []

    if not items:
        for spec in get_metrics_registry().list_specs():
            d = spec.to_dict()
            if layer and d["layer"] != layer:
                continue
            if pillar and d["pillar"] != pillar:
                continue
            if business and d["business"] != business:
                continue
            d["labels"] = d.get("labels", [])
            d["thresholds"] = d.get("thresholds", {})
            items.append(d)

    return {"items": items, "total": len(items)}
