"""
职责: 写标 express 在就绪态调用已有确认/生成/匹配/撰写入口。
不做: 不上传素材、不投影知识库、不改目录生成或匹配算法。
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.project import Project, ProjectStatus
from app.models.user import User
from app.models.writing_binding import BindingStatus, WritingBinding
from app.models.writing_clause import WritingClause
from app.services.express_flow.run_mode import revert_run_mode_to_guided
from app.services.writing.clause_readiness import HIDDEN_CONFIRMATION_SUB_TYPES
from app.services.writing_service import WritingService


class WritingExpressService:
    """
    职责: 按默认集合确认约束/绑定并续跑目录、匹配、撰写。
    不做: 不处理素材文件，不打开人工确认页。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def advance_express_writing(self, project: Project, user: User) -> dict[str, object]:
        if project.status == ProjectStatus.EXTRACTED:
            return await self._advance_extracted(project, user)
        if project.status == ProjectStatus.OUTLINED:
            return await self._advance_outlined(project, user)
        if project.status == ProjectStatus.MATCHED:
            return await self._advance_matched(project, user)
        return {"action": "skipped", "reason": "not_ready"}

    async def _advance_extracted(self, project: Project, user: User) -> dict[str, object]:
        writing = WritingService(self.db)
        try:
            clause_ids = await self._unconfirmed_visible_clause_ids(project.id)
            if clause_ids:
                await writing.confirm_clauses(project.id, clause_ids, user)
            await writing.generate_outline(project.id, user)
            return {"action": "started_outline", "confirmed_clauses": len(clause_ids)}
        except ServiceError as exc:
            return await self._revert(project, exc.detail)

    async def _advance_outlined(self, project: Project, user: User) -> dict[str, object]:
        try:
            await WritingService(self.db).trigger_match(project.id, user)
            return {"action": "started_match"}
        except ServiceError as exc:
            return await self._revert(project, exc.detail)

    async def _advance_matched(self, project: Project, user: User) -> dict[str, object]:
        writing = WritingService(self.db)
        try:
            await self._reject_orphan_bindings(project.id)
            binding_ids = await self._suggested_bound_ids(project.id)
            if binding_ids:
                await writing.confirm_bindings(project.id, binding_ids, user)
            await writing.trigger_compose(project.id, user)
            return {"action": "started_compose", "confirmed_bindings": len(binding_ids)}
        except ServiceError as exc:
            return await self._revert(project, exc.detail)

    async def _unconfirmed_visible_clause_ids(self, project_id: int) -> list[int]:
        result = await self.db.execute(
            select(WritingClause.id).where(
                WritingClause.project_id == project_id,
                WritingClause.confirmed == 0,
                WritingClause.sub_type.notin_(HIDDEN_CONFIRMATION_SUB_TYPES),
            )
        )
        return [int(row[0]) for row in result.all()]

    async def _suggested_bound_ids(self, project_id: int) -> list[int]:
        result = await self.db.execute(
            select(WritingBinding.id).where(
                WritingBinding.project_id == project_id,
                WritingBinding.status == BindingStatus.suggested,
                WritingBinding.outline_node_id.isnot(None),
            )
        )
        return [int(row[0]) for row in result.all()]

    async def _reject_orphan_bindings(self, project_id: int) -> None:
        await self.db.execute(
            update(WritingBinding)
            .where(
                WritingBinding.project_id == project_id,
                WritingBinding.status == BindingStatus.suggested,
                WritingBinding.outline_node_id.is_(None),
            )
            .values(status=BindingStatus.rejected, reason="一步到位：未匹配约束按忽略")
        )
        await self.db.flush()

    async def _revert(self, project: Project, reason: str) -> dict[str, object]:
        logger.warning(
            "写标一步到位推进失败，回退人工确认: project_id={}, detail={}",
            project.id,
            reason,
        )
        revert_run_mode_to_guided(project)
        await self.db.flush()
        return {"action": "reverted_guided", "reason": reason}
