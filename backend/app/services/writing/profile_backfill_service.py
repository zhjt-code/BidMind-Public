"""章节画像存量回填服务。

职责: 对已有目录树幂等重跑章节画像生成，不重建目录、不触发匹配。
不做: 不生成目录树、不写绑定、不调用素材匹配、不处理 HTTP。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.core.writer.material_matcher import MaterialMatcher
from app.core.writer.outline_generator import get_outline_generator
from app.models.project import Project, ProjectStatus
from app.models.user import User
from app.models.writing_outline_node import WritingOutlineNode

_ALLOWED_STATUSES = frozenset({
    ProjectStatus.OUTLINED,
    ProjectStatus.MATCHING,
    ProjectStatus.MATCHED,
    ProjectStatus.WRITING,
    ProjectStatus.COMPOSED,
    ProjectStatus.FORMATTING,
    ProjectStatus.COMPLETED,
})


class ProfileBackfillService:
    """存量项目章节画像回填。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        project_resolver: Callable[..., Awaitable[Project]],
    ) -> None:
        self.db = db
        self._resolve_project = project_resolver

    async def regenerate_chapter_profiles(
        self,
        project_id: int,
        user: User,
    ) -> dict[str, Any]:
        """幂等重跑章节画像，覆盖已有 chapter_profile 字段。

        Returns:
            含 profiled / leaf_total / quality_stats 的结果摘要。
        """
        project = await self._resolve_project(project_id, user)
        if project.status not in _ALLOWED_STATUSES:
            raise ServiceError(
                f"当前状态 {project.status.value} 不允许回填章节画像，"
                "请先完成目录生成"
            )

        leaf_total = await self._count_leaf_nodes(project_id)
        if leaf_total <= 0:
            raise ServiceError("项目尚无目录叶子节点，无法回填章节画像")

        generator = get_outline_generator(db=self.db, project_id=project_id)
        profiled = await generator.generate_chapter_profiles(project_id)

        # 重新加载叶子节点统计质量（与匹配侧口径一致）
        nodes = await self._load_outline_nodes(project_id)
        child_ids = {n.parent_id for n in nodes if n.parent_id is not None}
        leaf_chapters = [n for n in nodes if n.id not in child_ids]
        quality_stats = MaterialMatcher._assess_chapter_profile_quality(leaf_chapters)

        logger.info(
            "章节画像回填完成: project={}, profiled={}/{}, ok={}, degraded={}",
            project_id,
            profiled,
            leaf_total,
            quality_stats.get("ok", 0),
            quality_stats.get("degraded", 0),
        )
        return {
            "project_id": project_id,
            "profiled": profiled,
            "leaf_total": leaf_total,
            "quality_stats": quality_stats,
            "message": (
                f"章节画像回填完成：成功 {profiled}/{leaf_total}，"
                f"可用 {quality_stats.get('ok', 0)}，"
                f"degraded {quality_stats.get('degraded', 0)}"
            ),
        }

    async def _count_leaf_nodes(self, project_id: int) -> int:
        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
            )
        )
        nodes = list(result.scalars().all())
        if not nodes:
            return 0
        child_ids = {n.parent_id for n in nodes if n.parent_id is not None}
        return sum(1 for n in nodes if n.id not in child_ids)

    async def _load_outline_nodes(
        self, project_id: int,
    ) -> list[WritingOutlineNode]:
        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
            ).order_by(WritingOutlineNode.depth, WritingOutlineNode.sort_order)
        )
        return list(result.scalars().all())


__all__ = ["ProfileBackfillService"]
