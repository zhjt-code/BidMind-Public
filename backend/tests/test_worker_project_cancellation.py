"""Worker 项目取消语义测试。"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import worker as worker_module


@pytest.mark.asyncio
async def test_resource_permit_rechecks_project_cancelled_before_operation(monkeypatch):
    """资源许可等待后若项目已取消, worker 不应继续执行业务 operation。"""

    operation_called = False
    checked_projects: list[int] = []

    @asynccontextmanager
    async def fake_permit_context(redis, resource_type, limit, owner):
        yield True

    @contextmanager
    def fake_cpu_sticky_scope():
        yield

    async def fake_is_project_cancelled_async(redis, project_id: int) -> bool:
        checked_projects.append(project_id)
        return True

    async def operation() -> None:
        nonlocal operation_called
        operation_called = True

    monkeypatch.setattr(
        "app.core.tasks.resource_permit.permit_context",
        fake_permit_context,
    )
    monkeypatch.setattr(
        "app.core.tasks.resource_policy.policy_for_function",
        lambda function_name: SimpleNamespace(resource_type="cpu", max_jobs=1),
    )
    monkeypatch.setattr(
        "app.core.vectorizer.encode_gate.task_cpu_sticky_scope",
        fake_cpu_sticky_scope,
    )
    monkeypatch.setattr(
        "app.worker.is_project_cancelled_async",
        fake_is_project_cancelled_async,
    )

    await worker_module._run_with_resource_permit(
        {"redis": object(), "bidmind_worker_id": "worker-1"},
        "process_document_job",
        operation,
        project_id=77,
        task_type="process",
    )

    assert checked_projects == [77]
    assert operation_called is False


@pytest.mark.asyncio
async def test_resource_permit_exits_when_project_cancelled_after_wait_timeout(monkeypatch):
    """未获取资源许可后若项目已取消, worker 应立即退出而不是继续 sleep 排队。"""

    operation_called = False
    checked_projects: list[int] = []

    @asynccontextmanager
    async def fake_permit_context(redis, resource_type, limit, owner):
        yield False

    async def fake_is_project_cancelled_async(redis, project_id: int) -> bool:
        checked_projects.append(project_id)
        return True

    async def fake_sleep(delay: float) -> None:
        raise AssertionError("worker should exit when project is cancelled")

    async def operation() -> None:
        nonlocal operation_called
        operation_called = True

    monkeypatch.setattr(
        "app.core.tasks.resource_permit.permit_context",
        fake_permit_context,
    )
    monkeypatch.setattr(
        "app.core.tasks.resource_policy.policy_for_function",
        lambda function_name: SimpleNamespace(resource_type="cpu", max_jobs=1),
    )
    monkeypatch.setattr(
        "app.worker.is_project_cancelled_async",
        fake_is_project_cancelled_async,
    )
    monkeypatch.setattr(worker_module.asyncio, "sleep", fake_sleep)

    await worker_module._run_with_resource_permit(
        {"redis": object(), "bidmind_worker_id": "worker-1"},
        "process_document_job",
        operation,
        project_id=78,
        task_type="process",
    )

    assert checked_projects == [78]
    assert operation_called is False
