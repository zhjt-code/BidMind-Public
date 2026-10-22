""": 全文生成后应回扫无稿/空稿/失败稿。"""

from types import SimpleNamespace

from app.core.writer.composer.compose_sweep import should_sweep_section


def _draft(**kwargs):
    base = dict(
        content="<p>有效正文</p>",
        word_count=10,
        failure_state="completed",
        is_user_edited=0,
        is_locked=0,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_missing_draft_should_sweep() -> None:
    assert should_sweep_section(None) is True


def test_empty_current_draft_should_sweep() -> None:
    assert should_sweep_section(_draft(content="", word_count=0)) is True


def test_failed_current_draft_should_sweep() -> None:
    assert should_sweep_section(_draft(failure_state="failed")) is True


def test_user_edited_draft_should_not_sweep() -> None:
    assert should_sweep_section(_draft(content="", word_count=0, is_user_edited=1)) is False


def test_locked_draft_should_not_sweep() -> None:
    assert should_sweep_section(_draft(failure_state="failed", is_locked=1)) is False


def test_completed_content_should_not_sweep() -> None:
    assert should_sweep_section(_draft()) is False
