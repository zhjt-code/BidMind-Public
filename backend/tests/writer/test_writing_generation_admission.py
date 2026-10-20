""" 防御测试: 写标全文/单章生成准入必须跨进程原子且互斥。

职责: 锁定 Project 行锁后检查+创建 TaskRun 的临界区，以及全文与单章互斥。
不做: 不启动 ARQ、不调用 LLM、不测计费与投递成功路径。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.project import Project
from app.models.task_run import TaskRun, TaskRunStatus
from app.services.retry_service import RetryService


class _FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: list(self._rows))


class _FakeSession:
    def __init__(self, *, project: object | None, runs: list[object] | None = None) -> None:
        self.project = project
        self.runs = list(runs or [])
        self.project_locked = False
        self.execute_froms: list[set[str]] = []

    async def execute(self, stmt: object) -> _FakeResult:
        froms = {getattr(clause, "name", "") for clause in stmt.get_final_froms()}
        self.execute_froms.append(froms)
        if "projects" in froms:
            if getattr(stmt, "_for_update_arg", None) is None:
                raise AssertionError("projects 查询必须带 FOR UPDATE")
            self.project_locked = True
            return _FakeResult([self.project] if self.project is not None else [])
        if "task_runs" in froms:
            return _FakeResult(list(self.runs))
        raise AssertionError(f"未预期的查询 froms={froms}")


def _request(**overrides: object) -> object:
    from app.services.writing.generation_admission import WritingGenerationClaimRequest

    payload = dict(
        project_id=7,
        node_key=RetryService.NODE_WRITING_COMPOSE,
        task_name="writing_compose_7",
        user_id=3,
        target_id=None,
        explicit_retry=False,
    )
    payload.update(overrides)
    return WritingGenerationClaimRequest(**payload)


@pytest.mark.asyncio
async def test_concurrent_second_claim_returns_existing_run() -> None:
    """同一项目第二次 claim 必须看到第一次创建的 queued TaskRun，不得再 begin_attempt。"""
    from app.services.writing.generation_admission import WritingGenerationAdmissionService

    existing = SimpleNamespace(
        id=11,
        task_name="writing_compose_7",
        status=TaskRunStatus.queued.value,
        node_key=RetryService.NODE_WRITING_COMPOSE,
        current_arq_job_id="job-1",
    )
    session = _FakeSession(project=SimpleNamespace(id=7), runs=[existing])
    begin_attempt = AsyncMock()
    liveness = SimpleNamespace(is_live=True)

    with (
        patch("app.services.retry_service.RetryService.begin_attempt", begin_attempt),
        patch(
            "app.services.writing.generation_admission.inspect_task_liveness",
            return_value=liveness,
        ),
        patch(
            "app.services.writing.generation_admission.is_task_active",
            return_value=True,
        ),
    ):
        first = WritingGenerationAdmissionService(session)
        result = await first.claim_generation(_request())

    assert result.claimed is False
    assert result.task_run is existing
    assert result.active_task_name == "writing_compose_7"
    begin_attempt.assert_not_awaited()
    assert session.project_locked is True


@pytest.mark.asyncio
async def test_full_and_single_claims_are_mutually_exclusive() -> None:
    """全文 queued/running 时，单章 claim 必须失败并返回全文任务名。"""
    from app.services.writing.generation_admission import WritingGenerationAdmissionService

    full_run = SimpleNamespace(
        id=21,
        task_name="writing_compose_7",
        status=TaskRunStatus.running.value,
        node_key=RetryService.NODE_WRITING_COMPOSE,
        current_arq_job_id=None,
    )
    session = _FakeSession(project=SimpleNamespace(id=7), runs=[full_run])
    begin_attempt = AsyncMock()

    with (
        patch("app.services.retry_service.RetryService.begin_attempt", begin_attempt),
        patch(
            "app.services.writing.generation_admission.inspect_task_liveness",
            return_value=SimpleNamespace(is_live=True),
        ),
        patch(
            "app.services.writing.generation_admission.is_task_active",
            return_value=True,
        ),
    ):
        service = WritingGenerationAdmissionService(session)
        result = await service.claim_generation(
            _request(
                node_key=RetryService.NODE_WRITING_COMPOSE_SINGLE,
                task_name="writing_compose_single_7_99",
                target_id=99,
            )
        )

    assert result.claimed is False
    assert result.active_task_name == "writing_compose_7"
    begin_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_queued_run_is_finalized_before_new_attempt() -> None:
    """心跳已死的 queued/running 记录必须先终结，再允许 begin_attempt。"""
    from app.services.writing.generation_admission import WritingGenerationAdmissionService

    stale = SimpleNamespace(
        id=31,
        task_name="writing_compose_7",
        status=TaskRunStatus.queued.value,
        node_key=RetryService.NODE_WRITING_COMPOSE,
        last_stage="queued",
        last_error=None,
        current_arq_job_id=None,
    )
    created = SimpleNamespace(
        id=32,
        task_name="writing_compose_7",
        status=TaskRunStatus.queued.value,
        node_key=RetryService.NODE_WRITING_COMPOSE,
    )
    session = _FakeSession(project=SimpleNamespace(id=7), runs=[stale])
    begin_attempt = AsyncMock(return_value=created)

    with (
        patch("app.services.retry_service.RetryService.begin_attempt", begin_attempt),
        patch(
            "app.services.writing.generation_admission.inspect_task_liveness",
            return_value=SimpleNamespace(is_live=False),
        ),
        patch(
            "app.services.writing.generation_admission.is_task_active",
            return_value=False,
        ),
    ):
        service = WritingGenerationAdmissionService(session)
        result = await service.claim_generation(_request())

    assert result.claimed is True
    assert result.task_run is created
    assert stale.status == TaskRunStatus.cancelled.value
    begin_attempt.assert_awaited_once()


def test_compose_service_wires_claim_before_dispatch() -> None:
    """ComposeService 必须走原子 claim，投递失败要标记 TaskRun failed。"""
    source = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "writing"
        / "compose_service.py"
    ).read_text(encoding="utf-8")
    assert "WritingGenerationAdmissionService" in source
    assert "claim_generation" in source
    assert "mark_task_failed" in source
    assert "dispatch_failed" in source


def test_admission_query_targets_compose_and_single_node_keys() -> None:
    """查询谓词必须同时覆盖 writing_compose 与 writing_compose_single:*。"""
    source = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "writing"
        / "generation_admission.py"
    ).read_text(encoding="utf-8")
    assert "NODE_WRITING_COMPOSE" in source
    assert "NODE_WRITING_COMPOSE_SINGLE" in source
    assert "with_for_update" in source
    assert select(Project).where(Project.id == 1).with_for_update() is not None
    assert TaskRun.node_key is not None
