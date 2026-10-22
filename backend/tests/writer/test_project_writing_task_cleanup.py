""" 防御测试: 项目删除必须按 TaskRun 精确任务名取消写标生成。

职责: 锁定收集 writing_compose / writing_compose_single:{node} 的 task_name 与 arq_job_id。
不做: 不删除项目行、不写墓碑、不测其它流程前缀。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.task_run import TaskRunStatus
from app.services.retry_service import RetryService


class _FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: list(self._rows))


class _FakeSession:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self.rows)


@pytest.mark.asyncio
async def test_cleanup_cancels_exact_single_task_name_not_truncated_prefix() -> None:
    """带 node_id 的真实任务名必须被取消，截断前缀 writing_compose_single_{pid} 不够。"""
    from app.services.project_task_cleanup import ProjectWritingTaskCleanup

    exact_name = "writing_compose_single_12_34"
    truncated = "writing_compose_single_12"
    run = SimpleNamespace(
        task_name=exact_name,
        current_arq_job_id="arq-single-34",
        node_key=f"{RetryService.NODE_WRITING_COMPOSE_SINGLE}:34",
        status=TaskRunStatus.running.value,
    )
    session = _FakeSession([run])
    cancel = Mock(return_value=True)
    task_obj = SimpleNamespace(done=lambda: False)

    with (
        patch("app.services.project_task_cleanup.cancel_task", cancel),
        patch("app.services.project_task_cleanup.get_task", return_value=task_obj),
    ):
        cleanup = ProjectWritingTaskCleanup(session)
        targets = await cleanup.collect_targets(12)
        result = cleanup.cancel_local_tasks(targets)

    assert exact_name in targets.task_names
    assert truncated not in targets.task_names
    assert "arq-single-34" in targets.arq_job_ids
    cancel.assert_called_with(exact_name)
    assert exact_name in result.cancelled_names
    assert task_obj in result.tasks_to_await


@pytest.mark.asyncio
async def test_cleanup_includes_full_compose_task_and_job_id() -> None:
    from app.services.project_task_cleanup import ProjectWritingTaskCleanup

    run = SimpleNamespace(
        task_name="writing_compose_12",
        current_arq_job_id="arq-full-12",
        node_key=RetryService.NODE_WRITING_COMPOSE,
        status=TaskRunStatus.queued.value,
    )
    cleanup = ProjectWritingTaskCleanup(_FakeSession([run]))
    targets = await cleanup.collect_targets(12)
    assert targets.task_names == ["writing_compose_12"]
    assert targets.arq_job_ids == ["arq-full-12"]


def test_project_service_delete_uses_cleanup_helper() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "services" / "project_service.py"
    ).read_text(encoding="utf-8")
    assert "ProjectWritingTaskCleanup" in source
    assert "collect_targets" in source
    assert "cancel_local_tasks" in source
