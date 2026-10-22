"""
职责: 审标 express 在 extracted 就绪态调用已有 confirm_all + start_review。
不做: 不提取招标要求、不执行审查本体、不改计费预占策略。
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.project import Project, ProjectStatus
from app.models.user import User
from app.services.express_flow.run_mode import revert_run_mode_to_guided
from app.services.requirement_service import RequirementService
from app.services.review_service import ReviewService


class ReviewExpressService:
    """
    职责: 按默认集合确认可评审项并启动审查。
    不做: 不提升参考/风险折叠项，不配置知识库。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def advance_express_review(self, project: Project, user: User) -> dict[str, object]:
        if project.status != ProjectStatus.EXTRACTED:
            return {"action": "skipped", "reason": "not_extracted"}

        try:
            await RequirementService(self.db).confirm_all(project.id, user)
            result = await ReviewService(self.db).start_review(project.id, user)
            return {"action": "started_review", "result": result}
        except ServiceError as exc:
            logger.warning(
                "审标一步到位推进失败，回退人工确认: project_id={}, detail={}",
                project.id,
                exc.detail,
            )
            revert_run_mode_to_guided(project)
            await self.db.flush()
            return {"action": "reverted_guided", "reason": exc.detail}
