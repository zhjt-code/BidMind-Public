"""Minimal business flow orchestrator guards.

第一阶段只做轻量前置依赖检查, 不接管全部业务流程。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project, ProjectStatus
from app.models.writing_outline_node import WritingOutlineNode


StepDecision = Literal["ready", "waiting_dependency", "missing_project"]


@dataclass(frozen=True)
class StepGuardResult:
    """业务节点释放判定。"""

    decision: StepDecision
    reason: str = ""


async def guard_writing_material_process(
    db: AsyncSession,
    project_id: int,
) -> StepGuardResult:
    """写标素材处理必须在约束/目录阶段后释放。

    该 guard 不改变业务数据, 只防止后置节点抢跑。
    """

    project = await db.get(Project, project_id)
    if project is None:
        return StepGuardResult("missing_project", "project_not_found")

    allowed_status = {
        ProjectStatus.OUTLINED,
        ProjectStatus.MATCHING,
        ProjectStatus.MATCHED,
        ProjectStatus.WRITING,
        ProjectStatus.COMPOSED,
        ProjectStatus.FORMATTING,
        ProjectStatus.COMPLETED,
    }
    if project.status not in allowed_status:
        return StepGuardResult(
            "waiting_dependency",
            f"project_status_not_ready:{getattr(project.status, 'value', project.status)}",
        )

    result = await db.execute(
        select(WritingOutlineNode.id).where(
            WritingOutlineNode.project_id == project_id,
        ).limit(1)
    )
    if result.scalar_one_or_none() is None:
        return StepGuardResult("waiting_dependency", "outline_not_ready")

    return StepGuardResult("ready")
