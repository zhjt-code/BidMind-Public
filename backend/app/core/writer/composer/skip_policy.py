"""SectionComposer 生成跳过策略。

职责: 判断批量内容生成时是否应跳过某个章节节点。
不做: 不生成内容、不查询数据库、不修改节点或草稿状态。
"""

from __future__ import annotations

from typing import Any

from app.models.writing_outline_node import OutlineNodeStatus


def _status_value(status: Any) -> str:
    """兼容枚举与字符串状态。"""
    return status.value if hasattr(status, "value") else str(status or "")


def should_skip_compose_node(
    node: Any,
    current_draft: Any | None,
    skip_existing_drafts: bool,
) -> bool:
    """判断批量生成是否应跳过该章节。"""
    if _status_value(getattr(node, "status", "")) == OutlineNodeStatus.finalized.value:
        return True
    if current_draft is None:
        return False
    if bool(getattr(current_draft, "is_user_edited", 0)) or bool(
        getattr(current_draft, "is_locked", 0)
    ):
        return True
    return bool(skip_existing_drafts)


__all__ = ["should_skip_compose_node"]
