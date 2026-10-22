"""项目绑定释放相关测试。"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.core.tasks as tasks_module
from app.core.tasks import cancel_task, cleanup_task, create_background_task
from app.core.tasks.writing_compose_task import run_writing_compose_task
from app.models.project import ProjectStatus, ProjectType


@pytest.mark.asyncio
async def test_cancel_task_schedules_binding_release(monkeypatch):
    released: list[int] = []

    def _fake_release(project_id: int) -> None:
        released.append(project_id)

    monkeypatch.setattr(tasks_module, "_schedule_release_project_binding", _fake_release)

    async def _sleepy():
        await asyncio.sleep(1)

    task_name = "unit_cancel_release"
    create_background_task(_sleepy(), name=task_name, project_id=515)

    assert cancel_task(task_name) is True
    await asyncio.sleep(0)
    cleanup_task(task_name)

    assert 515 in released


@pytest.mark.asyncio
async def test_task_completion_schedules_binding_release(monkeypatch):
    released: list[int] = []

    def _fake_release(project_id: int) -> None:
        released.append(project_id)

    monkeypatch.setattr(tasks_module, "_schedule_release_project_binding", _fake_release)

    async def _done():
        return None

    task_name = "unit_done_release"
    task = create_background_task(_done(), name=task_name, project_id=516)
    await task
    await asyncio.sleep(0)

    assert 516 in released


@pytest.mark.asyncio
async def test_writing_compose_releases_binding_after_success(monkeypatch):
    project = SimpleNamespace(
        id=517,
        project_type=ProjectType.BID_WRITING,
        status=ProjectStatus.CREATED,
        error_message=None,
    )
    released: list[int] = []

    class _FakeSession:
        async def get(self, model, project_id):
            assert project_id == 517
            return project

        async def commit(self):
            return None

    class _SessionContext:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _FakeComposer:
        async def compose_all(self, project_id, progress_callback=None, **kwargs):
            assert project_id == 517
            if progress_callback:
                progress_callback("composing", 1, 1, "done")
            return ["draft-1"]

    monkeypatch.setattr(
        "app.core.tasks.writing_compose_task.async_session_factory",
        lambda: _SessionContext(),
    )
    monkeypatch.setattr(
        "app.core.tasks.update_heartbeat",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.core.writer.section_composer.get_section_composer",
        lambda db, project_id: _FakeComposer(),
    )

    # RetryService 调用 db.execute()，_FakeSession 不支持，需整体 stub。
    # 注意 RetryService.NODE_WRITING_COMPOSE 是类属性，必须保留在 stub 类上。
    class _FakeRetryService:
        NODE_WRITING_COMPOSE = "writing_compose"

        def __init__(self, db):
            pass

        async def mark_task_running(self, *args, **kwargs):
            pass

        async def mark_task_completed(self, *args, **kwargs):
            pass

        async def mark_task_failed(self, *args, **kwargs):
            pass

    monkeypatch.setattr("app.services.retry_service.RetryService", _FakeRetryService)
    # sweep_incomplete_sections 会访问 db，需 stub 为空返回
    monkeypatch.setattr(
        "app.core.writer.composer.compose_sweep.sweep_incomplete_sections",
        AsyncMock(return_value=[]),
    )
    # _refresh_consistency_cache_after_compose 调用 db.execute()，需 stub
    monkeypatch.setattr(
        "app.core.tasks.writing_compose_task._refresh_consistency_cache_after_compose",
        AsyncMock(return_value=0),
    )
    # BillingGuard.on_completed 需要真实 db，测试中不涉及计费逻辑
    monkeypatch.setattr(
        "app.billing.services.billing_guard.BillingGuard.on_completed",
        AsyncMock(),
    )

    async def _fake_release(project_id: int) -> None:
        released.append(project_id)

    monkeypatch.setattr("app.core.ai.release_project_binding", _fake_release)

    await run_writing_compose_task(517)

    assert project.status == ProjectStatus.COMPOSED
    assert released == [517]
