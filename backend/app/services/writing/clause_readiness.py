"""写标约束确认门控。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.writing_clause import WritingClause

HIDDEN_CONFIRMATION_SUB_TYPES = {
    "bid_structure_analysis",
    "bid_directory_spec",
}


class WritingClauseReadinessService:
    """
    职责: 校验写标后续阶段启动前的可见约束确认状态。
    不做: 不生成目录/匹配/正文，不修改约束内容，不触发向量化。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def ensure_visible_clauses_confirmed(self, project_id: int) -> None:
        """可见约束仍有未确认项时阻断下游阶段。"""
        if not hasattr(self.db, "execute"):
            return
        result = await self.db.execute(
            select(func.count()).select_from(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.confirmed == 0,
                WritingClause.sub_type.notin_(HIDDEN_CONFIRMATION_SUB_TYPES),
            )
        )
        unconfirmed_count = int(result.scalar_one() or 0)
        if unconfirmed_count <= 0:
            return
        raise ServiceError(
            f"还有 {unconfirmed_count} 条约束未确认，请先完成约束确认",
            status_code=400,
        )
