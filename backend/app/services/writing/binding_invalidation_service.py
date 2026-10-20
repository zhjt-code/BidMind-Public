"""标书编写绑定变更失效服务。

职责: 根据绑定变更计算受影响章节并失效草稿复核状态与渲染一致性缓存。
不做: 不执行重匹配、不生成正文、不调用 LLM。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.writing_render_artifact import WritingRenderArtifact
from app.models.writing_section_draft import WritingSectionDraft


_BINDING_CHANGED_REASON = "绑定已变更，请重新生成章节"


class BindingInvalidationService:
    """绑定变更后的草稿与渲染缓存失效服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def mark_changed_bindings_stale(
        self,
        project_id: int,
        bindings: list[Any],
    ) -> int:
        """标记绑定影响的当前草稿需复核，并清空渲染缓存。"""
        node_ids = _collect_impacted_node_ids(bindings)
        updated = 0
        if node_ids:
            result = await self.db.execute(
                select(WritingSectionDraft).where(
                    WritingSectionDraft.outline_node_id.in_(node_ids),
                    WritingSectionDraft.is_current == 1,
                )
            )
            for draft in result.scalars().all():
                draft.review_required = 1
                draft.failure_state = "needs_review"
                draft.failure_reason = _BINDING_CHANGED_REASON
                updated += 1

        await self._invalidate_render_artifact(project_id)
        return updated

    async def _invalidate_render_artifact(self, project_id: int) -> None:
        result = await self.db.execute(
            select(WritingRenderArtifact).where(
                WritingRenderArtifact.project_id == project_id
            )
        )
        artifact = result.scalars().first()
        if artifact is None:
            return
        artifact.preview_html = None
        artifact.docx_blob = None
        artifact.docx_size = None
        artifact.rendered_at = None
        artifact.consistency_issues_json = None
        artifact.consistency_checked_at = None
        artifact.consistency_render_version = None


def _collect_impacted_node_ids(bindings: list[Any]) -> list[int]:
    node_ids: list[int] = []
    seen: set[int] = set()
    for binding in bindings:
        node_id = getattr(binding, "outline_node_id", None)
        if node_id is None:
            continue
        try:
            node_id_int = int(node_id)
        except (TypeError, ValueError):
            continue
        if node_id_int in seen:
            continue
        seen.add(node_id_int)
        node_ids.append(node_id_int)
    return node_ids


__all__ = ["BindingInvalidationService"]
