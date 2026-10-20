""": 空产出不能落为 completed 草稿。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.writer.composer.draft_persistence import classify_draft_failure_reason


class _ScalarRows:
    def scalars(self):
        return self

    def all(self):
        return []


class _FakeSession:
    def __init__(self):
        self.added = None
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    async def execute(self, *_args, **_kwargs):
        return _ScalarRows()

    def add(self, obj):
        self.added = obj


def test_empty_html_is_failed_draft() -> None:
    assert classify_draft_failure_reason("<p>   </p>") == "章节生成结果为空"


def test_failed_placeholder_is_failed_draft() -> None:
    reason = classify_draft_failure_reason("<p>[质量认证证书 内容生成失败，请手动补充]</p>")

    assert reason == "章节生成失败占位内容"


def test_normal_content_is_not_failed_draft() -> None:
    assert classify_draft_failure_reason("<p>本章已根据素材生成有效内容。</p>") is None


@pytest.mark.asyncio
async def test_save_draft_counts_plain_text_not_html_markup() -> None:
    """BUG: 落库 word_count 必须与长度闭环相同，统计纯文本而非 HTML 源码。"""
    from app.core.writer.composer.draft_persistence import save_draft

    db = _FakeSession()
    composer = SimpleNamespace(
        db=db,
        _db_lock=asyncio.Lock(),
        _serialize_source_ids=lambda ids: "[]" if not ids else str(ids),
    )
    node = SimpleNamespace(id=7, title="资质证明")

    draft = await save_draft(
        composer,
        node,
        "<h4>资格</h4><p>正文 [待补充:证书编号]</p><figure data-image-id=\"1\"></figure>",
    )

    assert draft.word_count == len("资格正文")
    assert db.added is draft
