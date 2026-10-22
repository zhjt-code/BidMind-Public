"""ops_metrics.metrics_rules: /metrics/rules endpoint 业务装配 (R2-8 拆分产物).

由 admin_ops.py list_metric_rules 内 SQL + 装配迁出。
safe_json_dict 通过 kwargs 依赖注入透传。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def list_metric_rules(
    *,
    db: AsyncSession,
    enabled_only: bool,
    metric_name: Optional[str],
    safe_json_dict: Callable[[Optional[str]], dict],
) -> dict[str, Any]:
    """只读列出 metric_rule 表 (Phase 4). 编辑留到后续 Phase."""
    from app.models.metric_rule import MetricRule

    stmt = select(MetricRule)
    if enabled_only:
        stmt = stmt.where(MetricRule.enabled == True)  # noqa: E712
    if metric_name:
        stmt = stmt.where(MetricRule.metric_name == metric_name)
    rows = (await db.execute(stmt.order_by(MetricRule.metric_name))).scalars().all()

    items = [
        {
            "id": r.id,
            "metric_name": r.metric_name,
            "rule_type": r.rule_type,
            "algorithm": r.algorithm,
            "params": safe_json_dict(r.params_json),
            "verdict_map": safe_json_dict(r.verdict_map_json),
            "enabled": r.enabled,
            "updated_by": r.updated_by,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "description": r.description,
        }
        for r in rows
    ]

    return {"items": items, "total": len(items)}
