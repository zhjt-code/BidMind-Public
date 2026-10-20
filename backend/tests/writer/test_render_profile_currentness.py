from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, clauses, profile) -> None:
        self._results = [_ScalarResult(clauses), _ScalarResult([profile] if profile else [])]
        self.execute = AsyncMock(side_effect=self._results)
        self.delete = AsyncMock()
        self.commit = AsyncMock()


def _format_clause(clause_id: int, updated_at: datetime):
    return SimpleNamespace(
        id=clause_id,
        sub_type="format_rule",
        content="格式要求",
        raw_text="格式原文",
        updated_at=updated_at,
    )


def _submission_clause(clause_id: int, updated_at: datetime):
    return SimpleNamespace(
        id=clause_id,
        sub_type="submission_rule",
        content="正本一份副本三份，密封提交",
        raw_text="提交规则原文",
        updated_at=updated_at,
    )


def test_render_profile_source_sub_types_include_submission_rules() -> None:
    """: render_profile 的源约束类型必须覆盖成品形态提交规定。"""
    from app.core.writer.render_profile_currentness import RENDER_PROFILE_SOURCE_SUB_TYPES

    assert RENDER_PROFILE_SOURCE_SUB_TYPES == {
        "format_rule",
        "submission_rule",
        "submission_notice",
    }


@pytest.mark.asyncio
async def test_stale_render_profile_refreshes_from_confirmed_format_rules(monkeypatch) -> None:
    from app.core.writer import render_profile_currentness

    now = datetime.now(timezone.utc)
    clause = _format_clause(1001, now)
    profile = SimpleNamespace(
        source_clause_ids="[1001]",
        updated_at=now - timedelta(minutes=5),
    )
    db = _FakeDb([clause], profile)
    extractor = SimpleNamespace(parse_render_profile=AsyncMock())
    monkeypatch.setattr(
        render_profile_currentness,
        "get_clause_extractor",
        MagicMock(return_value=extractor),
    )

    await render_profile_currentness.ensure_render_profile_current(db, project_id=9)

    extractor.parse_render_profile.assert_awaited_once_with(9, [clause])


@pytest.mark.asyncio
async def test_submission_rules_are_render_profile_sources(monkeypatch) -> None:
    """: 成品形态提交规则必须进入 render_profile 解析源。"""
    from app.core.writer import render_profile_currentness

    now = datetime.now(timezone.utc)
    format_clause = _format_clause(1001, now)
    submission_clause = _submission_clause(1002, now)
    profile = SimpleNamespace(
        source_clause_ids="[1001]",
        updated_at=now,
    )
    db = _FakeDb([format_clause, submission_clause], profile)
    extractor = SimpleNamespace(parse_render_profile=AsyncMock())
    monkeypatch.setattr(
        render_profile_currentness,
        "get_clause_extractor",
        MagicMock(return_value=extractor),
    )

    await render_profile_currentness.ensure_render_profile_current(db, project_id=9)

    extractor.parse_render_profile.assert_awaited_once_with(
        9, [format_clause, submission_clause],
    )


@pytest.mark.asyncio
async def test_current_render_profile_does_not_refresh(monkeypatch) -> None:
    from app.core.writer import render_profile_currentness

    now = datetime.now(timezone.utc)
    clause = _format_clause(1001, now - timedelta(minutes=5))
    profile = SimpleNamespace(
        source_clause_ids="[1001]",
        updated_at=now,
    )
    db = _FakeDb([clause], profile)
    extractor = SimpleNamespace(parse_render_profile=AsyncMock())
    monkeypatch.setattr(
        render_profile_currentness,
        "get_clause_extractor",
        MagicMock(return_value=extractor),
    )

    await render_profile_currentness.ensure_render_profile_current(db, project_id=9)

    extractor.parse_render_profile.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_removed_format_rules_delete_stale_render_profile() -> None:
    from app.core.writer import render_profile_currentness

    profile = SimpleNamespace(source_clause_ids="[1001]", updated_at=datetime.now(timezone.utc))
    db = _FakeDb([], profile)

    await render_profile_currentness.ensure_render_profile_current(db, project_id=9)

    db.delete.assert_awaited_once_with(profile)
    db.commit.assert_awaited_once()
