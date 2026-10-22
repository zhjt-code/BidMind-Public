"""ops_metrics.related_events: hotspots 关联事件聚合 (R2-8 拆分产物).

由 admin_ops.py _collect_related_events 迁出,通过 thin forwarder 委托。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_execution_trace import LlmExecutionTrace
from app.models.system_error_log import SystemErrorLog


async def collect_related_events(
    db: AsyncSession,
    *,
    cs: datetime,
    now: datetime,
    name: str,
) -> dict[str, Any]:
    """返回近窗口最近 N 条 LlmExecutionTrace / error_log / prompt_versions 变更."""
    del now, name
    related: dict[str, Any] = {
        "llm_execution_trace": [],
        "system_error_log": [],
        "prompt_version_changes": [],
    }

    try:
        traces = (await db.execute(
            select(
                LlmExecutionTrace.id,
                LlmExecutionTrace.created_at,
                LlmExecutionTrace.project_id,
            )
            .where(LlmExecutionTrace.created_at >= cs)
            .order_by(LlmExecutionTrace.created_at.desc())
            .limit(10)
        )).all()
        related["llm_execution_trace"] = [
            {"id": t_id, "created_at": t_ct.isoformat() if t_ct else None, "project_id": pid}
            for t_id, t_ct, pid in traces
        ]
    except Exception:
        pass

    try:
        errs = (await db.execute(
            select(
                SystemErrorLog.id,
                SystemErrorLog.created_at,
                SystemErrorLog.level,
                SystemErrorLog.module,
                SystemErrorLog.message,
            )
            .where(SystemErrorLog.created_at >= cs)
            .where(SystemErrorLog.resolved == False)  # noqa: E712
            .order_by(SystemErrorLog.created_at.desc())
            .limit(10)
        )).all()
        related["system_error_log"] = [
            {
                "id": e_id,
                "created_at": e_ct.isoformat() if e_ct else None,
                "level": level,
                "module": module,
                "message": (message or "")[:200],
            }
            for e_id, e_ct, level, module, message in errs
        ]
    except Exception:
        pass

    try:
        from app.models.prompt_version import PromptVersion
        pvs = (await db.execute(
            select(
                PromptVersion.id,
                PromptVersion.purpose,
                PromptVersion.version,
                PromptVersion.name,
                PromptVersion.is_active,
                PromptVersion.updated_at,
            )
            .where(PromptVersion.updated_at >= cs)
            .order_by(PromptVersion.updated_at.desc())
            .limit(5)
        )).all()
        related["prompt_version_changes"] = [
            {
                "id": pid,
                "purpose": purpose,
                "version": version,
                "name": pv_name,
                "is_active": bool(is_active),
                "updated_at": upd.isoformat() if upd else None,
            }
            for pid, purpose, version, pv_name, is_active, upd in pvs
        ]
    except Exception:
        pass

    return related
