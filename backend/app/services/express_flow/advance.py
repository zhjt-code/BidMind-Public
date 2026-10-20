"""
职责: 按项目类型与状态把 express 推进分发给审标/写标服务。
不做: 不实现确认或启动细节，不处理串标。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ServiceError
from app.models.project import Project, ProjectType
from app.services.express_flow.review_express_service import ReviewExpressService
from app.services.express_flow.run_mode import is_express_run_mode
from app.services.express_flow.writing_express_service import WritingExpressService


async def advance_express_project(db: AsyncSession, project_id: int) -> dict[str, object]:
    """在后台任务中按当前就绪态推进一次 express。幂等：非 express / 非就绪态 no-op。"""
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.owner))
        .where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"项目不存在: id={project_id}")

    if not is_express_run_mode(project.run_mode):
        return {"action": "skipped", "reason": "not_express"}

    if project.project_type == ProjectType.COLLUSION_CHECK:
        return {"action": "skipped", "reason": "collusion"}

    user = project.owner
    if user is None:
        raise ServiceError("项目没有所有者，无法自动推进")

    if project.project_type == ProjectType.BID_REVIEW:
        return await ReviewExpressService(db).advance_express_review(project, user)
    if project.project_type == ProjectType.BID_WRITING:
        return await WritingExpressService(db).advance_express_writing(project, user)
    return {"action": "skipped", "reason": "unknown_type"}
