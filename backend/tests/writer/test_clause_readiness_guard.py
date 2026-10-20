from types import SimpleNamespace

import pytest

from app.core.exceptions import ServiceError


class _CountResult:
    def __init__(self, count: int) -> None:
        self._count = count

    def scalar_one(self) -> int:
        return self._count


class _FakeDb:
    def __init__(self, count: int) -> None:
        self.count = count
        self.executed = []

    async def execute(self, statement):
        self.executed.append(statement)
        return _CountResult(self.count)


@pytest.mark.asyncio
async def test_visible_unconfirmed_clauses_block_writer_downstream_steps() -> None:
    from app.services.writing.clause_readiness import WritingClauseReadinessService

    service = WritingClauseReadinessService(_FakeDb(count=2))

    with pytest.raises(ServiceError) as exc:
        await service.ensure_visible_clauses_confirmed(project_id=101)

    assert exc.value.status_code == 400
    assert "2 条约束未确认" in exc.value.detail
    assert "约束确认" in exc.value.detail


@pytest.mark.asyncio
async def test_no_visible_unconfirmed_clauses_allows_downstream_steps() -> None:
    from app.services.writing.clause_readiness import WritingClauseReadinessService

    db = _FakeDb(count=0)
    service = WritingClauseReadinessService(db)

    await service.ensure_visible_clauses_confirmed(project_id=101)

    assert len(db.executed) == 1


@pytest.mark.asyncio
async def test_non_database_test_doubles_do_not_block_downstream_unit_tests() -> None:
    from app.services.writing.clause_readiness import WritingClauseReadinessService

    service = WritingClauseReadinessService(SimpleNamespace())

    await service.ensure_visible_clauses_confirmed(project_id=101)


def test_hidden_system_clause_sub_types_are_not_user_confirmation_blockers() -> None:
    from app.services.writing.clause_readiness import HIDDEN_CONFIRMATION_SUB_TYPES

    assert HIDDEN_CONFIRMATION_SUB_TYPES == {
        "bid_structure_analysis",
        "bid_directory_spec",
    }
