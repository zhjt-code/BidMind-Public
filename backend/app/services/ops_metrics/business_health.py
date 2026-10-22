"""ops_metrics.business_health: 三业务健康聚合 (R2-8 拆分产物).

由 admin_ops.py _business_health 迁出,通过 thin forwarder 委托。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.vector_job_state import (
    ACTIVE_VECTOR_JOB_STATES,
    VectorJobState,
    VectorJobStatus,
)


async def business_health(
    *,
    db: AsyncSession,
    cs: datetime,
    bs: datetime,
) -> dict[str, Any]:
    """三业务健康条: 招标 / 围标 / 写作 三条.

    Phase 1 用 Project.project_type 和 VectorJobState 组合判断; Phase 2 接入 task_observation 后更准。
    """
    del bs

    def _ptype_key(pt: Any) -> Optional[str]:
        if pt is None:
            return None
        return pt.value if hasattr(pt, "value") else str(pt)

    rows = (await db.execute(
        select(Project.project_type, func.count(VectorJobState.id))
        .join(VectorJobState, VectorJobState.project_id == Project.id)
        .where(VectorJobState.status.in_([s for s in ACTIVE_VECTOR_JOB_STATES]))
        .group_by(Project.project_type)
    )).all()
    active_by_type: dict[str, int] = {}
    for t, c in rows:
        key = _ptype_key(t)
        if key:
            active_by_type[key] = c

    fail_rows = (await db.execute(
        select(Project.project_type, func.count(VectorJobState.id))
        .join(VectorJobState, VectorJobState.project_id == Project.id)
        .where(VectorJobState.status == VectorJobStatus.FAILED)
        .where(VectorJobState.finished_at >= cs)
        .group_by(Project.project_type)
    )).all()
    fail_by_type: dict[str, int] = {}
    for t, c in fail_rows:
        key = _ptype_key(t)
        if key:
            fail_by_type[key] = c

    def _status(active: int, failed: int) -> str:
        if failed >= 3:
            return "critical"
        if failed >= 1 or active >= 10:
            return "warn"
        return "healthy"

    def _build(flow_key: str, project_types: list[str], label: str) -> dict[str, Any]:
        active = sum(active_by_type.get(t, 0) for t in project_types)
        failed = sum(fail_by_type.get(t, 0) for t in project_types)
        return {
            "flow": flow_key,
            "label": label,
            "active": active,
            "failed_in_window": failed,
            "status": _status(active, failed),
        }

    return {
        "tender": _build("tender", ["bid_review", "review"], "招标审查"),
        "collusion": _build("collusion", ["collusion_check", "collusion"], "围标分析"),
        "writer": _build("writer", ["bid_writing", "writing"], "标书写作"),
    }
