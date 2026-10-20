""": compose_all 跳过语义必须保护草稿内容，而非目录结构编辑态。"""

from types import SimpleNamespace

from app.core.writer.composer.skip_policy import should_skip_compose_node
from app.models.writing_outline_node import OutlineNodeStatus


def _node(status: OutlineNodeStatus | str):
    return SimpleNamespace(id=1, title="公司简介", status=status)


def _draft(*, is_user_edited: int = 0, is_locked: int = 0):
    return SimpleNamespace(is_user_edited=is_user_edited, is_locked=is_locked)


def test_user_edited_outline_without_draft_should_not_skip_first_generation() -> None:
    assert should_skip_compose_node(_node(OutlineNodeStatus.user_edited), None, False) is False


def test_user_edited_draft_should_skip_to_protect_user_content() -> None:
    assert (
        should_skip_compose_node(
            _node(OutlineNodeStatus.user_edited),
            _draft(is_user_edited=1),
            False,
        )
        is True
    )


def test_locked_draft_should_skip_even_when_node_status_is_pending() -> None:
    assert should_skip_compose_node(_node(OutlineNodeStatus.pending), _draft(is_locked=1), False) is True


def test_skip_existing_drafts_keeps_explicit_retry_behavior() -> None:
    assert should_skip_compose_node(_node(OutlineNodeStatus.pending), _draft(), True) is True


def test_finalized_node_still_skips_without_draft() -> None:
    assert should_skip_compose_node(_node(OutlineNodeStatus.finalized), None, False) is True
