""" 防御测试: 节点失败原因兜底链。

历史缺陷: `writing_tender_process` 首轮运行不建 TaskRun, heartbeat 又在任务结束
即被清除, 导致 retry-state 的 last_error 恒为 None, 前端只能显示"系统未返回具体原因",
而真实原因其实已落在 vector_job_states.last_error / projects.error_message。

本文件锁定两件事:
1. 四级优先级的选取顺序与空值语义
2. 持久化取数的有效性门控(状态不对的记录不得被当作当前失败原因)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest

from app.models.project import ProjectStatus
from app.models.vector_job_state import VectorJobStatus
from app.services.retry.failure_reason_resolver import (
    MAX_REASON_LENGTH,
    PersistedFailureSources,
    load_persisted_failure_sources,
    pick_failure_reason,
)

REAL_REASON = "未生成有效文本分块或向量，请检查文档是否为扫描件、空文件或已损坏"


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """只记录调用轨迹的最小 AsyncSession 替身。"""

    def __init__(
        self,
        *,
        project: Any = None,
        job_state: Any = None,
    ) -> None:
        self._project = project
        self._job_state = job_state
        self.execute_calls = 0
        self.get_calls: list[int] = []

    async def execute(self, _stmt: Any) -> _FakeResult:
        self.execute_calls += 1
        return _FakeResult(self._job_state)

    async def get(self, _model: Any, primary_key: int) -> Any:
        self.get_calls.append(primary_key)
        return self._project


def _project(status: ProjectStatus, error_message: Optional[str]) -> SimpleNamespace:
    return SimpleNamespace(id=1, status=status, error_message=error_message)


def _job_state(status: VectorJobStatus, last_error: Optional[str]) -> SimpleNamespace:
    return SimpleNamespace(document_id=2180, status=status, last_error=last_error)


# ==================== 优先级链 ====================


def test_task_run_error_wins_over_all_other_sources() -> None:
    assert (
        pick_failure_reason(
            task_run_error="来自 TaskRun",
            heartbeat_error="来自心跳",
            explicit_fallback="来自调用方",
            persisted=PersistedFailureSources(
                vector_job_error="来自向量任务",
                project_error="来自项目",
            ),
        )
        == "来自 TaskRun"
    )


def test_heartbeat_used_when_task_run_missing() -> None:
    assert (
        pick_failure_reason(
            task_run_error=None,
            heartbeat_error="来自心跳",
            explicit_fallback="来自调用方",
            persisted=PersistedFailureSources(project_error="来自项目"),
        )
        == "来自心跳"
    )


def test_explicit_fallback_outranks_persisted_sources() -> None:
    """目录人工调整这类处置说明比通用项目级错误更有指导价值。"""
    guidance = "已检测到人工调整目录，系统不会自动清理或合并目录，请人工确认后重新生成"

    assert (
        pick_failure_reason(
            task_run_error=None,
            heartbeat_error=None,
            explicit_fallback=guidance,
            persisted=PersistedFailureSources(project_error="来自项目"),
        )
        == guidance
    )


def test_vector_job_error_outranks_project_error() -> None:
    assert (
        pick_failure_reason(
            task_run_error=None,
            heartbeat_error=None,
            persisted=PersistedFailureSources(
                vector_job_error="来自向量任务",
                project_error="来自项目",
            ),
        )
        == "来自向量任务"
    )


def test_project_error_is_last_resort() -> None:
    assert (
        pick_failure_reason(
            task_run_error=None,
            heartbeat_error="",
            persisted=PersistedFailureSources(project_error="来自项目"),
        )
        == "来自项目"
    )


def test_bug795_shape_no_task_run_no_heartbeat_but_vector_job_has_reason() -> None:
    """本次故障的真实形态: 首轮无 TaskRun + 心跳已清除 + 向量任务留有原因。"""
    assert (
        pick_failure_reason(
            task_run_error=None,
            heartbeat_error="",
            persisted=PersistedFailureSources(vector_job_error=REAL_REASON),
        )
        == REAL_REASON
    )


def test_blank_sources_are_treated_as_missing() -> None:
    assert (
        pick_failure_reason(
            task_run_error="   ",
            heartbeat_error="\n\t",
            explicit_fallback="",
            persisted=PersistedFailureSources(vector_job_error="  ", project_error="真原因"),
        )
        == "真原因"
    )


def test_all_sources_empty_returns_none() -> None:
    assert (
        pick_failure_reason(
            task_run_error=None,
            heartbeat_error="",
            explicit_fallback=None,
            persisted=PersistedFailureSources(),
        )
        is None
    )
    assert pick_failure_reason(task_run_error=None, heartbeat_error=None) is None


def test_reason_is_truncated_to_shared_length() -> None:
    picked = pick_failure_reason(task_run_error="长" * 900, heartbeat_error=None)

    assert picked is not None
    assert len(picked) == MAX_REASON_LENGTH


# ==================== 持久化取数门控 ====================


@pytest.mark.asyncio
async def test_loader_reads_both_sources_when_failed() -> None:
    session = _FakeSession(
        project=_project(ProjectStatus.FAILED, REAL_REASON),
        job_state=_job_state(VectorJobStatus.FAILED, REAL_REASON),
    )

    sources = await load_persisted_failure_sources(
        session, project_id=1, document_id=2180  # type: ignore[arg-type]
    )

    assert sources.vector_job_error == REAL_REASON
    assert sources.project_error == REAL_REASON
    assert session.execute_calls == 1


@pytest.mark.asyncio
async def test_loader_skips_vector_job_query_without_document_id() -> None:
    """素材等无文档维度的节点不应产生多余查询。"""
    session = _FakeSession(project=_project(ProjectStatus.FAILED, "项目级原因"))

    sources = await load_persisted_failure_sources(session, project_id=1)  # type: ignore[arg-type]

    assert session.execute_calls == 0
    assert sources.vector_job_error is None
    assert sources.project_error == "项目级原因"


@pytest.mark.asyncio
async def test_loader_ignores_project_error_when_project_not_failed() -> None:
    """项目已恢复运行时，历史 error_message 不得再被当作当前失败原因。"""
    session = _FakeSession(project=_project(ProjectStatus.PARSING, "上一轮的陈旧原因"))

    sources = await load_persisted_failure_sources(session, project_id=1)  # type: ignore[arg-type]

    assert sources.project_error is None


@pytest.mark.asyncio
async def test_loader_ignores_vector_job_error_when_job_completed() -> None:
    """upsert 不清空 last_error，成功重跑后的陈旧值必须被状态门控挡住。"""
    session = _FakeSession(
        project=_project(ProjectStatus.PARSING, None),
        job_state=_job_state(VectorJobStatus.COMPLETED, "上一轮失败原因"),
    )

    sources = await load_persisted_failure_sources(
        session, project_id=1, document_id=2180  # type: ignore[arg-type]
    )

    assert sources.vector_job_error is None


@pytest.mark.asyncio
async def test_loader_accepts_lost_vector_job_as_failure_source() -> None:
    session = _FakeSession(
        project=_project(ProjectStatus.PARSING, None),
        job_state=_job_state(VectorJobStatus.LOST, "任务被中断，等待自动恢复"),
    )

    sources = await load_persisted_failure_sources(
        session, project_id=1, document_id=2180  # type: ignore[arg-type]
    )

    assert sources.vector_job_error == "任务被中断，等待自动恢复"


@pytest.mark.asyncio
async def test_loader_tolerates_missing_rows() -> None:
    session = _FakeSession()

    sources = await load_persisted_failure_sources(
        session, project_id=1, document_id=2180  # type: ignore[arg-type]
    )

    assert sources == PersistedFailureSources()
