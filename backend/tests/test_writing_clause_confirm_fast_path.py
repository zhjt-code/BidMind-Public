import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.tasks.writing_clause_vectorize_task import run_writing_clause_vectorize_task
from app.services.writing_service import WritingService


class _FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalarResult(self._items)


@pytest.mark.asyncio
async def test_confirm_clauses_marks_confirmed_and_defers_vectorization() -> None:
    clauses = [
        SimpleNamespace(id=1, project_id=7, confirmed=0),
        SimpleNamespace(id=2, project_id=7, confirmed=0),
    ]
    fake_db = SimpleNamespace(
        execute=AsyncMock(return_value=_FakeExecuteResult(clauses)),
        commit=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=7))  # type: ignore[method-assign]
    service._ensure_clause_vectorize_task = AsyncMock()  # type: ignore[method-assign]

    confirmed = await service.confirm_clauses(7, [1, 2], SimpleNamespace())

    assert confirmed == 2
    assert [c.confirmed for c in clauses] == [1, 1]
    fake_db.commit.assert_awaited_once()
    service._ensure_clause_vectorize_task.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_ensure_clause_vectorize_task_skips_duplicate_active_task(monkeypatch) -> None:
    service = WritingService(SimpleNamespace())
    create_task_mock = Mock()

    monkeypatch.setattr("app.services.writing_service.is_task_active", lambda _name: True)
    monkeypatch.setattr("app.services.writing_service.create_background_task", create_task_mock)

    await service._ensure_clause_vectorize_task(11)

    create_task_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_clause_vectorize_task_starts_background_task(monkeypatch) -> None:
    """ARQ 不可用(enqueue_job_detailed 返回 queued=False) 时, 必须回退到 create_background_task。"""
    service = WritingService(SimpleNamespace())
    create_task_mock = Mock()

    async def fake_run(project_id: int) -> None:
        return None

    # enqueue_job_detailed 是 dispatch_background_job 内实际调用的函数
    async def fake_enqueue_detailed(function_name: str, *args, **kwargs):
        # 模拟 ARQ 不可用 (开关关 / 无 worker / Redis 不可用)
        return SimpleNamespace(
            queued=False,
            function_name=function_name,
            reason="prefer_arq_disabled",
            job_id=None,
            queue_name="arq:queue",
            should_wait_for_worker=False,
        )

    monkeypatch.setattr("app.services.writing_service.is_task_active", lambda _name: False)
    # dispatch_background_job 在 app.core.tasks.__init__ 内调用 create_background_task，
    # 需 patch 该模块内的引用而非 writing_service 的引用
    monkeypatch.setattr("app.core.tasks.create_background_task", create_task_mock)
    monkeypatch.setattr("app.core.arq_runtime.enqueue_job_detailed", fake_enqueue_detailed)
    monkeypatch.setattr(
        "app.core.tasks.writing_clause_vectorize_task.run_writing_clause_vectorize_task",
        fake_run,
    )

    await service._ensure_clause_vectorize_task(13)

    create_task_mock.assert_called_once()
    args, kwargs = create_task_mock.call_args
    assert inspect.iscoroutine(args[0])
    args[0].close()
    assert kwargs["name"] == "writing_clause_vectorize_13"
    assert kwargs["task_type"] == "writing_clause_vectorize"


@pytest.mark.asyncio
async def test_ensure_clause_vectorize_task_prefers_arq_when_available(monkeypatch) -> None:
    """ARQ 可用(enqueue_job_safe 返回 job_id) 时, 不应再调用 create_background_task。"""
    service = WritingService(SimpleNamespace())
    create_task_mock = Mock()

    async def fake_enqueue_ok(function_name: str, *args, **kwargs):
        assert function_name == "writing_clause_vectorize_job"
        assert args == (17,)
        return "job-arq-17"

    monkeypatch.setattr("app.services.writing_service.is_task_active", lambda _name: False)
    monkeypatch.setattr("app.services.writing_service.create_background_task", create_task_mock)
    monkeypatch.setattr("app.core.arq_runtime.enqueue_job_safe", fake_enqueue_ok)

    await service._ensure_clause_vectorize_task(17)

    # ARQ 已接管, asyncio 回退路径不应被调用
    create_task_mock.assert_not_called()


class _FakeSessionFactory:
    """可复用的 async context manager，返回固定 db_session。"""

    def __init__(self, db_session=None):
        self._db = db_session or SimpleNamespace()

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_run_writing_clause_vectorize_task_rechecks_for_newly_confirmed_items(monkeypatch) -> None:
    heartbeat_mock = Mock()

    class _FakeService:
        call_count = 0

        def __init__(self, db):
            self.db = db

        async def _vectorize_confirmed_clauses(self, project_id: int) -> int:
            self.__class__.call_count += 1
            return [2, 1, 0][self.__class__.call_count - 1]

    monkeypatch.setattr("app.core.tasks.update_heartbeat", heartbeat_mock)
    monkeypatch.setattr(
        "app.core.tasks.writing_clause_vectorize_task.async_session_factory",
        lambda: _FakeSessionFactory(),
    )
    monkeypatch.setattr("app.services.writing_service.WritingService", _FakeService)

    await run_writing_clause_vectorize_task(17)

    assert _FakeService.call_count == 3
    assert heartbeat_mock.call_args_list[-1].kwargs["stage"] == "completed"


@pytest.mark.asyncio
async def test_vectorize_task_reports_failed_on_exception(monkeypatch) -> None:
    """_vectorize_confirmed_clauses 抛异常时 heartbeat 必须为 failed。"""
    heartbeat_mock = Mock()

    class _FailingService:
        def __init__(self, db):
            self.db = db

        async def _vectorize_confirmed_clauses(self, project_id: int) -> int:
            raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr("app.core.tasks.update_heartbeat", heartbeat_mock)
    monkeypatch.setattr(
        "app.core.tasks.writing_clause_vectorize_task.async_session_factory",
        lambda: _FakeSessionFactory(),
    )
    monkeypatch.setattr("app.services.writing_service.WritingService", _FailingService)

    await run_writing_clause_vectorize_task(99)

    final_call = heartbeat_mock.call_args_list[-1]
    assert final_call.kwargs["stage"] == "failed"
    assert "CUDA out of memory" in final_call.kwargs["progress"]


@pytest.mark.asyncio
async def test_vectorize_task_completes_when_nothing_to_process(monkeypatch) -> None:
    """无待向量化记录时 heartbeat 仍为 completed。"""
    heartbeat_mock = Mock()

    class _EmptyService:
        def __init__(self, db):
            self.db = db

        async def _vectorize_confirmed_clauses(self, project_id: int) -> int:
            return 0

    monkeypatch.setattr("app.core.tasks.update_heartbeat", heartbeat_mock)
    monkeypatch.setattr(
        "app.core.tasks.writing_clause_vectorize_task.async_session_factory",
        lambda: _FakeSessionFactory(),
    )
    monkeypatch.setattr("app.services.writing_service.WritingService", _EmptyService)

    await run_writing_clause_vectorize_task(50)

    final_call = heartbeat_mock.call_args_list[-1]
    assert final_call.kwargs["stage"] == "completed"
