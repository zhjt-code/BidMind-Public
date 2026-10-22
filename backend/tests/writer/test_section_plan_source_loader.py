from types import SimpleNamespace

import pytest


class _AsyncNoopLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _FakeDb:
    def __init__(self, values):
        self.values = values
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self.values)


@pytest.mark.asyncio
async def test_load_section_plan_clauses_adds_confirmed_project_context() -> None:
    """: 工期等章节必须能消费项目级确认背景（如14组测试）。"""
    from app.core.writer.section_plan.source_loader import load_section_plan_clauses

    bound = SimpleNamespace(id=17911, sub_type="content_required")
    project_context = SimpleNamespace(id=17991, sub_type="project_context")
    composer = SimpleNamespace(
        db=_FakeDb([project_context]),
        _db_lock=_AsyncNoopLock(),
    )

    result = await load_section_plan_clauses(
        composer,
        project_id=2120,
        bound_clauses=[bound],
    )

    assert [item.id for item in result] == [17911, 17991]
    assert composer.db.statements


@pytest.mark.asyncio
async def test_load_section_plan_clauses_deduplicates_existing_project_context() -> None:
    from app.core.writer.section_plan.source_loader import load_section_plan_clauses

    project_context = SimpleNamespace(id=17991, sub_type="project_context")
    composer = SimpleNamespace(
        db=_FakeDb([project_context]),
        _db_lock=_AsyncNoopLock(),
    )

    result = await load_section_plan_clauses(
        composer,
        project_id=2120,
        bound_clauses=[project_context],
    )

    assert [item.id for item in result] == [17991]


@pytest.mark.asyncio
async def test_load_section_plan_clauses_uses_task_cache_once() -> None:
    from app.core.writer.composer.compose_cache import ComposeTaskCache
    from app.core.writer.section_plan.source_loader import load_section_plan_clauses

    bound = SimpleNamespace(id=17911, sub_type="content_required")
    project_context = SimpleNamespace(id=17991, sub_type="project_context")
    cache = ComposeTaskCache(project_id=2120)
    composer = SimpleNamespace(
        db=_FakeDb([project_context]),
        _db_lock=_AsyncNoopLock(),
        _compose_cache=cache,
    )

    first = await load_section_plan_clauses(
        composer,
        project_id=2120,
        bound_clauses=[bound],
    )
    second = await load_section_plan_clauses(
        composer,
        project_id=2120,
        bound_clauses=[bound],
    )

    assert [item.id for item in first] == [item.id for item in second] == [17911, 17991]
    assert len(composer.db.statements) == 1
