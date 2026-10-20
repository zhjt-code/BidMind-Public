"""ops_metrics.vector_jobs: /vector-jobs/distribution endpoint 业务装配 (R2-8 拆分产物).

由 admin_ops.py vector_job_distribution 迁出,简单 SQL + 装配。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vector_job_state import (
    ACTIVE_VECTOR_JOB_STATES,
    TERMINAL_VECTOR_JOB_STATES,
    VectorJobState,
    VectorJobStatus,
)


async def get_vector_job_distribution(db: AsyncSession) -> dict[str, Any]:
    """向量任务状态分布 (用于 /admin/ops 子图)."""
    rows = (await db.execute(
        select(VectorJobState.status, func.count(VectorJobState.id))
        .group_by(VectorJobState.status)
    )).all()
    breakdown: dict[str, int] = {s.value: 0 for s in VectorJobStatus}
    for st, cnt in rows:
        key = st.value if hasattr(st, "value") else str(st)
        breakdown[key] = cnt
    return {
        "breakdown": breakdown,
        "active_total": sum(breakdown[s.value] for s in ACTIVE_VECTOR_JOB_STATES),
        "terminal_total": sum(breakdown[s.value] for s in TERMINAL_VECTOR_JOB_STATES),
    }
