"""SectionComposer 批量生成回头看补偿。

职责: 批量生成收尾后对无草稿/空草稿/失败草稿的叶子章节做一轮补偿重生成并汇总结果。
不做: 生成路由实现、一致性检查、用户编辑草稿的覆盖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

if TYPE_CHECKING:
    from app.core.writer.section_composer import SectionComposer
    from app.models.writing_outline_node import WritingOutlineNode
    from app.models.writing_section_draft import WritingSectionDraft


def should_sweep_section(current_draft: Any | None) -> bool:
    """判断某章节当前草稿是否需要补偿生成。"""
    if current_draft is None:
        return True
    if bool(getattr(current_draft, "is_user_edited", 0)) or bool(
        getattr(current_draft, "is_locked", 0)
    ):
        return False
    if str(getattr(current_draft, "failure_state", "") or "") == "failed":
        return True
    content = str(getattr(current_draft, "content", "") or "").strip()
    word_count = int(getattr(current_draft, "word_count", 0) or 0)
    return not content or word_count <= 0


async def _load_leaf_nodes(
    composer: "SectionComposer",
    project_id: int,
) -> list["WritingOutlineNode"]:
    """加载项目叶子章节。"""
    all_nodes = await composer._load_all_outline_nodes(project_id)
    parent_ids = {node.parent_id for node in all_nodes if node.parent_id is not None}
    return [node for node in all_nodes if node.id not in parent_ids]


async def sweep_incomplete_sections(
    composer: "SectionComposer",
    project_id: int,
    nodes: Optional[list["WritingOutlineNode"]] = None,
) -> list["WritingSectionDraft"]:
    """对未完成章节补偿重生成一次，返回本轮新增/更新的草稿。"""
    target_nodes = nodes if nodes is not None else await _load_leaf_nodes(composer, project_id)
    repaired: list["WritingSectionDraft"] = []
    for node in target_nodes:
        current = await composer._load_current_draft(node.id)
        if not should_sweep_section(current):
            continue
        logger.info("内容生成回头看补偿: project={}, node={}", project_id, node.title)
        try:
            draft = await composer.compose_single(node, project_id)
            repaired.append(draft)
            refreshed = await composer._load_current_draft(node.id)
            if should_sweep_section(refreshed):
                if refreshed is not None:
                    refreshed.review_required = 1
                    refreshed.failure_state = "needs_review"
                    refreshed.failure_reason = refreshed.failure_reason or "补偿生成后仍未得到有效正文"
                    async with composer._db_lock:
                        await composer.db.commit()
        except Exception as exc:  # noqa: BLE001 单章补偿失败不应拖垮全文任务
            logger.warning(
                "内容生成回头看补偿失败: project={}, node={}, err={}",
                project_id,
                node.title,
                exc,
                exc_info=True,
            )
            if current is not None:
                current.review_required = 1
                current.failure_state = "needs_review"
                current.failure_reason = f"补偿生成失败: {type(exc).__name__}: {str(exc)[:120]}"
                async with composer._db_lock:
                    await composer.db.commit()
    return repaired


__all__ = ["should_sweep_section", "sweep_incomplete_sections"]
