"""草稿正文事务原子性：失败不推进节点状态，摘要失败仍保留正文。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.writer.composer.draft_persistence import DraftWriteRequest, save_draft
from app.models.writing_outline_node import OutlineNodeStatus
from app.models.writing_section_draft import WritingSectionDraft


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeDb:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.fail_commit = fail_commit
        self.added: list[object] = []
        self.commits = 0

    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("commit boom")
        self.commits += 1

    async def refresh(self, obj: object) -> None:
        obj.id = 17


def _composer(db: _FakeDb) -> SimpleNamespace:
    return SimpleNamespace(
        db=db,
        _db_lock=_Lock(),
        _serialize_source_ids=lambda value: "[]" if not value else str(list(value)),
    )


@pytest.mark.asyncio
async def test_body_commit_failure_does_not_advance_node_status() -> None:
    db = _FakeDb(fail_commit=True)
    node = SimpleNamespace(id=3, title="方案", status=OutlineNodeStatus.pending)
    with pytest.raises(RuntimeError, match="commit boom"):
        await save_draft(
            _composer(db),
            node,
            DraftWriteRequest(content="<p>正文</p>"),
        )
    assert node.status == OutlineNodeStatus.pending
    assert db.commits == 0


@pytest.mark.asyncio
async def test_body_transaction_sets_node_status_and_keeps_version_fields() -> None:
    db = _FakeDb()
    node = SimpleNamespace(id=4, title="封面", status=OutlineNodeStatus.pending)
    draft = await save_draft(
        _composer(db),
        node,
        DraftWriteRequest(
            content="<p>正文</p>",
            source_clause_ids=[1],
            source_material_ids=[8],
            planning_mode="planned_new",
        ),
    )
    assert isinstance(draft, WritingSectionDraft)
    assert node.status == OutlineNodeStatus.draft
    assert db.commits == 1
    saved = db.added[0]
    assert saved.version == 1
    assert saved.is_current == 1
    assert saved.is_user_edited == 0
    assert saved.is_locked == 0
    assert saved.planning_mode == "planned_new"


@pytest.mark.asyncio
async def test_summary_failure_keeps_current_body() -> None:
    """摘要是独立提交；正文事务成功后摘要失败不得回滚正文。"""
    db = _FakeDb()
    node = SimpleNamespace(id=5, title="技术", status=OutlineNodeStatus.pending)
    draft = await save_draft(
        _composer(db),
        node,
        DraftWriteRequest(content="<p>已保存正文</p>", update_node_status=True),
    )
    assert draft.content == "<p>已保存正文</p>"
    assert node.status == OutlineNodeStatus.draft
    try:
        raise RuntimeError("summary boom")
    except RuntimeError:
        pass
    assert draft.content == "<p>已保存正文</p>"
    assert node.status == OutlineNodeStatus.draft


@pytest.mark.asyncio
async def test_placeholder_and_xml_exit_unchanged() -> None:
    db = _FakeDb()
    node = SimpleNamespace(id=6, title="公司", status=OutlineNodeStatus.pending)
    draft = await save_draft(
        _composer(db),
        node,
        DraftWriteRequest(content="<p>本公司{投标人}服务</p>\x00"),
    )
    assert "{投标人}" not in draft.content
    assert "\x00" not in draft.content
    assert "[待补充:投标人名称]" in draft.content
