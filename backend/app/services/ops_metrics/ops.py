"""ops_metrics.ops: 运营反馈 layer (R2-8 拆分产物).

由 admin_ops.py _layer_ops 迁出,通过 thin forwarder 委托。
verdict_from_ratio 通过 kwargs 依赖注入透传 (兼容 monkey patch)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.system_error_log import SystemErrorLog


async def layer_ops(
    *,
    db: AsyncSession,
    cs: datetime,
    bs: datetime,
    verdict_from_ratio: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    """运营反馈: 审计日志动作分布, top 错误模块."""
    audit_cur = (await db.execute(
        select(func.count(AuditLog.id))
        .where(AuditLog.created_at >= cs)
    )).scalar() or 0

    audit_base = (await db.execute(
        select(func.count(AuditLog.id))
        .where(AuditLog.created_at >= bs)
        .where(AuditLog.created_at < cs)
    )).scalar() or 0

    # 高频报错模块统计仅纳入 ERROR/CRITICAL 级别,
    # 与平台健康层口径保持一致,避免 WARNING 噪音淹没真正的故障模块。
    top_rows = (await db.execute(
        select(SystemErrorLog.module, func.count(SystemErrorLog.id).label("cnt"))
        .where(SystemErrorLog.created_at >= cs)
        .where(SystemErrorLog.module.isnot(None))
        .where(SystemErrorLog.level.in_(["ERROR", "CRITICAL"]))
        .group_by(SystemErrorLog.module)
        .order_by(func.count(SystemErrorLog.id).desc())
        .limit(5)
    )).all()
    top_modules = [{"module": m, "count": c} for m, c in top_rows]

    return [
        {
            "name": "audit_log_total_window",
            "layer": "ops",
            "pillar": "reliability",
            "direction": "neutral",
            "kind": "counter",
            "description": "近窗口审计日志条数",
            "value": float(audit_cur),
            "baseline": float(audit_base),
            **verdict_from_ratio(float(audit_cur), float(audit_base), "neutral"),
        },
        {
            "name": "top_error_modules",
            "layer": "ops",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "gauge",
            "description": "近窗口错误数 Top 5 模块 (仅 ERROR/CRITICAL,不含 WARNING)",
            "value": float(len(top_modules)),
            "baseline": 0.0,
            "verdict": "warn" if top_modules and top_modules[0]["count"] > 10 else "stable",
            "delta_pct": 0.0,
            "reason": "top 模块详情见 breakdown",
            "breakdown": top_modules,
        },
    ]
