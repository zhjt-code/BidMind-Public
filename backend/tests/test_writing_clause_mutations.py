from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.models.writing_clause import WritingClause
from app.schemas.writing import WritingClauseCreate
from app.services.writing_service import WritingService


class _FakeScalarResult:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None


class _FakeExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalarResult(self._items)


@pytest.mark.asyncio
async def test_create_clause_reuses_tender_document_and_inserts_after_anchor() -> None:
    tender_doc = SimpleNamespace(id=31)
    last_clause = SimpleNamespace(id=4, sort_order=3)
    shifted_clauses = [
        SimpleNamespace(id=4, project_id=7, sort_order=3),
        SimpleNamespace(id=3, project_id=7, sort_order=2),
    ]
    anchor_clause = SimpleNamespace(id=2, project_id=7, sort_order=1)
    added_records: list[WritingClause] = []

    async def refresh_side_effect(clause: WritingClause) -> None:
        clause.id = 99

    fake_db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _FakeExecuteResult([tender_doc]),
            _FakeExecuteResult([last_clause]),
            _FakeExecuteResult(shifted_clauses),
        ]),
        get=AsyncMock(return_value=anchor_clause),
        add=lambda clause: added_records.append(clause),
        commit=AsyncMock(),
        refresh=AsyncMock(side_effect=refresh_side_effect),
    )
    service = WritingService(fake_db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=7))  # type: ignore[method-assign]

    created = await service.create_clause(
        7,
        SimpleNamespace(),
        {
            "layer": "structure_constraint",
            "sub_type": "content_required",
            "content": "手工补充的内容要求",
            "insert_after_id": 2,
        },
    )

    assert created.id == 99
    assert created.document_id == 31
    assert created.is_manual == 1
    assert created.confirmed == 0
    assert created.sort_order == 2
    assert shifted_clauses[0].sort_order == 4
    assert shifted_clauses[1].sort_order == 3
    assert len(added_records) == 1
    fake_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_clause_removes_item_and_compacts_sort_order() -> None:
    target_clause = SimpleNamespace(id=5, project_id=7, sort_order=1)
    trailing_clauses = [
        SimpleNamespace(id=6, project_id=7, sort_order=2),
        SimpleNamespace(id=7, project_id=7, sort_order=3),
    ]
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=target_clause),
        delete=AsyncMock(),
        flush=AsyncMock(),
        execute=AsyncMock(return_value=_FakeExecuteResult(trailing_clauses)),
        commit=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=7))  # type: ignore[method-assign]

    await service.delete_clause(7, 5, SimpleNamespace())

    fake_db.delete.assert_awaited_once_with(target_clause)
    fake_db.flush.assert_awaited_once()
    fake_db.commit.assert_awaited_once()
    assert [c.sort_order for c in trailing_clauses] == [1, 2]


@pytest.mark.asyncio
async def test_update_confirmed_clause_resets_embedding_and_revectorizes() -> None:
    clause = SimpleNamespace(
        id=8,
        project_id=7,
        confirmed=1,
        content="旧内容",
        raw_text="旧原文",
        embedding=[0.1, 0.2],
    )
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=clause),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=7))  # type: ignore[method-assign]
    service._ensure_clause_vectorize_task = AsyncMock()  # type: ignore[method-assign]

    updated = await service.update_clause(
        7,
        8,
        SimpleNamespace(),
        {"content": "新内容"},
    )

    assert updated.content == "新内容"
    assert updated.embedding is None
    fake_db.commit.assert_awaited_once()
    service._ensure_clause_vectorize_task.assert_awaited_once_with(7)


def test_writing_clause_create_rejects_blank_content_and_unknown_sub_type() -> None:
    with pytest.raises(ValidationError):
        WritingClauseCreate(
            layer="structure_constraint",
            sub_type="unknown_type",
            content="   ",
        )
