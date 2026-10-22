"""ops_metrics.platform: 平台健康 layer (R2-8 拆分产物).

由 admin_ops.py _layer_platform 迁出,通过 thin forwarder 委托。
verdict_from_ratio 通过 kwargs 依赖注入透传 (兼容 monkey patch)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_error_log import SystemErrorLog


async def layer_platform(
    *,
    db: AsyncSession,
    cs: datetime,
    bs: datetime,
    verdict_from_ratio: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    """平台健康: 未处理错误, DB/Redis 可用性, 请求量(近窗口与基线).

    口径说明: 平台健康层只统计 ERROR/CRITICAL 级别,WARNING 不计入。
    WARNING 仅供错误日志明细页展示,不影响平台健康度判断。
    """
    unresolved_cur = (await db.execute(
        select(func.count(SystemErrorLog.id))
        .where(SystemErrorLog.resolved == False)  # noqa: E712
        .where(SystemErrorLog.level.in_(["ERROR", "CRITICAL"]))
        .where(SystemErrorLog.created_at >= cs)
    )).scalar() or 0

    unresolved_base = (await db.execute(
        select(func.count(SystemErrorLog.id))
        .where(SystemErrorLog.resolved == False)  # noqa: E712
        .where(SystemErrorLog.level.in_(["ERROR", "CRITICAL"]))
        .where(SystemErrorLog.created_at >= bs)
        .where(SystemErrorLog.created_at < cs)
    )).scalar() or 0

    critical_cur = (await db.execute(
        select(func.count(SystemErrorLog.id))
        .where(SystemErrorLog.level.in_(["ERROR", "CRITICAL"]))
        .where(SystemErrorLog.created_at >= cs)
    )).scalar() or 0

    critical_base = (await db.execute(
        select(func.count(SystemErrorLog.id))
        .where(SystemErrorLog.level.in_(["ERROR", "CRITICAL"]))
        .where(SystemErrorLog.created_at >= bs)
        .where(SystemErrorLog.created_at < cs)
    )).scalar() or 0

    redis_ok = 1.0
    try:
        from app.core.redis import get_redis_pool
        pool = await get_redis_pool()
        if pool:
            await pool.ping()
        else:
            redis_ok = 0.0
    except Exception:
        redis_ok = 0.0

    return [
        {
            "name": "unresolved_error_total",
            "layer": "platform",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "counter",
            "description": "近窗口新增的未处理错误日志数 (仅 ERROR/CRITICAL,不含 WARNING)",
            "value": unresolved_cur,
            "baseline": unresolved_base,
            **verdict_from_ratio(unresolved_cur, unresolved_base, "lower_better"),
        },
        {
            "name": "critical_error_total",
            "layer": "platform",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "counter",
            "description": "近窗口 ERROR/CRITICAL 级别日志数",
            "value": critical_cur,
            "baseline": critical_base,
            **verdict_from_ratio(critical_cur, critical_base, "lower_better"),
        },
        {
            "name": "redis_ready",
            "layer": "platform",
            "pillar": "reliability",
            "direction": "higher_better",
            "kind": "gauge",
            "description": "Redis 连接可达性 (1=可用, 0=不可用)",
            "value": redis_ok,
            "baseline": 1.0,
            "verdict": "stable" if redis_ok == 1.0 else "critical",
            "delta_pct": 0.0,
            "reason": "ping ok" if redis_ok == 1.0 else "ping failed",
        },
        {
            "name": "db_ready",
            "layer": "platform",
            "pillar": "reliability",
            "direction": "higher_better",
            "kind": "gauge",
            "description": "DB 可达性 (1=可用)",
            "value": 1.0,
            "baseline": 1.0,
            "verdict": "stable",
            "delta_pct": 0.0,
            "reason": "able to query error_logs",
        },
    ]
