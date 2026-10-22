""" 防御测试: 写标节点完成事实门控。

历史缺陷: `get_generic_retry_state` 把「心跳缺失/过期」直接当作节点可重试，
早已成功的招标解析节点在心跳 TTL 过期后被标成失败点，并吸附项目级失败文案
（"writing 任务异常终止"），前端失败入口停在上传页显示「招标文件解析失败」，
而真实失败节点是 writing_compose。

本文件锁定:
 1. resolve_node_completion 的事实判定（文档 parsed / TaskRun completed / 从未执行）
 2. get_generic_retry_state 接线后: 已完成节点不再产出 failed_node/last_error，
 真实失败节点行为不变，显式重试运行态不被门控吞掉
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.models.task_run import TaskRunStatus
from app.services.retry.failure_reason_resolver import PersistedFailureSources
from app.services.retry.node_completion_gate import (
    NodeCompletionVerdict,
    resolve_node_completion,
)
from app.services.retry_service import RetryService

ORPHAN_ERROR = "writing 任务异常终止，请从失败节点继续重试"


class _FakeDb:
    """最小 AsyncSession 替身: 门控只用 db.get 取 Document。"""

    def __init__(self, document: Any = None) -> None:
        self._document = document
        self.get_calls: list[Any] = []

    async def get(self, _model: Any, primary_key: Any) -> Any:
        self.get_calls.append(primary_key)
        return self._document


def _task_run(
    *,
    status: str,
    last_stage: Optional[str] = None,
    last_error: Optional[str] = None,
    attempt: int = 1,
    retry_requested_at: Optional[float] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        last_stage=last_stage,
        last_error=last_error,
        attempt=attempt,
        retry_requested_at=retry_requested_at,
    )


def _liveness(state: str, *, heartbeat: Optional[dict] = None) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        active=(state == "live"),
        is_live=(state == "live"),
        heartbeat=heartbeat,
        stage=str((heartbeat or {}).get("stage", "")),
        age_seconds=None,
        reason=state,
    )


# ==================== resolve_node_completion 单元 ====================


@pytest.mark.asyncio
async def test_tender_parsed_doc_is_completed() -> None:
    db = _FakeDb(document=SimpleNamespace(parsed=1))
    verdict = await resolve_node_completion(
        db,  # type: ignore[arg-type]
        is_tender_process=True,
        target_id=2306,
        latest_run=None,
        liveness_state="missing",
    )
    assert verdict.completed is True
    assert verdict.suppresses_failure is True


@pytest.mark.asyncio
async def test_tender_unparsed_doc_is_not_completed() -> None:
    """parsed=0 的真实解析失败不得被门控吞掉。"""
    db = _FakeDb(document=SimpleNamespace(parsed=0))
    verdict = await resolve_node_completion(
        db,  # type: ignore[arg-type]
        is_tender_process=True,
        target_id=2306,
        latest_run=None,
        liveness_state="missing",
    )
    assert verdict.completed is False
    assert verdict.never_attempted is False
    assert verdict.suppresses_failure is False


@pytest.mark.asyncio
async def test_tender_without_target_never_suppresses() -> None:
    db = _FakeDb()
    verdict = await resolve_node_completion(
        db,  # type: ignore[arg-type]
        is_tender_process=True,
        target_id=None,
        latest_run=None,
        liveness_state="missing",
    )
    assert verdict.suppresses_failure is False
    assert db.get_calls == [], "无 target_id 时不得查询文档"


@pytest.mark.asyncio
async def test_generic_completed_task_run_is_completed() -> None:
    verdict = await resolve_node_completion(
        _FakeDb(),  # type: ignore[arg-type]
        is_tender_process=False,
        target_id=None,
        latest_run=_task_run(status=TaskRunStatus.completed.value),
        liveness_state="missing",
    )
    assert verdict.completed is True


@pytest.mark.asyncio
async def test_generic_failed_task_run_is_not_completed() -> None:
    verdict = await resolve_node_completion(
        _FakeDb(),  # type: ignore[arg-type]
        is_tender_process=False,
        target_id=None,
        latest_run=_task_run(status=TaskRunStatus.failed.value),
        liveness_state="missing",
    )
    assert verdict.suppresses_failure is False


@pytest.mark.asyncio
async def test_generic_never_attempted_when_no_run_and_missing() -> None:
    verdict = await resolve_node_completion(
        _FakeDb(),  # type: ignore[arg-type]
        is_tender_process=False,
        target_id=None,
        latest_run=None,
        liveness_state="missing",
    )
    assert verdict.never_attempted is True
    assert verdict.suppresses_failure is True


@pytest.mark.asyncio
async def test_generic_no_run_but_stale_heartbeat_not_suppressed() -> None:
    """有过期心跳说明节点跑过（无 TaskRun 场景），不算从未执行。"""
    verdict = await resolve_node_completion(
        _FakeDb(),  # type: ignore[arg-type]
        is_tender_process=False,
        target_id=None,
        latest_run=None,
        liveness_state="stale",
    )
    assert verdict.suppresses_failure is False


def test_verdict_suppression_semantics() -> None:
    assert NodeCompletionVerdict().suppresses_failure is False
    assert NodeCompletionVerdict(completed=True).suppresses_failure is True
    assert NodeCompletionVerdict(never_attempted=True).suppresses_failure is True


# ==================== get_generic_retry_state 接线 ====================


async def _get_state(
    *,
    node_key: str,
    target_id: Optional[int] = None,
    latest: Optional[SimpleNamespace] = None,
    liveness_state: str = "missing",
    heartbeat: Optional[dict] = None,
    document: Any = None,
    persisted: Optional[PersistedFailureSources] = None,
) -> tuple[dict, AsyncMock]:
    """以最小替身驱动 get_generic_retry_state，返回 (payload, persisted_loader_mock)。"""
    svc = RetryService(_FakeDb(document=document))  # type: ignore[arg-type]
    persisted_loader = AsyncMock(
        return_value=persisted or PersistedFailureSources()
    )
    with (
        patch(
            "app.core.tasks.inspect_task_liveness",
            return_value=_liveness(liveness_state, heartbeat=heartbeat),
        ),
        patch.object(RetryService, "_latest_run", AsyncMock(return_value=latest)),
        patch(
            "app.services.retry_service.load_persisted_failure_sources",
            persisted_loader,
        ),
    ):
        payload = await svc.get_generic_retry_state(
            1, node_key=node_key, target_id=target_id
        )
    return payload, persisted_loader


@pytest.mark.asyncio
async def test_completed_extract_node_reports_no_failure() -> None:
    """已完成节点 + 心跳 missing → 不可重试、无失败节点、无失败原因（主症状）。"""
    payload, persisted_loader = await _get_state(
        node_key=RetryService.NODE_WRITING_EXTRACT,
        latest=_task_run(
            status=TaskRunStatus.completed.value,
            last_stage="completed",
            last_error=None,
            attempt=2,
        ),
        liveness_state="missing",
    )
    assert payload["retryable"] is False
    assert payload["failed_node"] is None
    assert payload["failed_stage"] is None
    assert payload["last_error"] is None
    assert payload["last_attempt"] == 2
    persisted_loader.assert_not_called(), "已完成节点不得吸附持久化失败文案"


@pytest.mark.asyncio
async def test_parsed_tender_node_reports_no_failure() -> None:
    """招标解析节点 parsed=1 + 心跳 missing → 不再显示「招标文件解析失败」。"""
    payload, persisted_loader = await _get_state(
        node_key=RetryService.NODE_WRITING_TENDER_PROCESS,
        target_id=2306,
        latest=None,
        liveness_state="missing",
        document=SimpleNamespace(parsed=1),
    )
    assert payload["retryable"] is False
    assert payload["failed_node"] is None
    assert payload["last_error"] is None
    persisted_loader.assert_not_called()


@pytest.mark.asyncio
async def test_unparsed_tender_failure_still_retryable() -> None:
    """parsed=0 的真实解析失败: 行为与修复前一致，可重试且带持久化原因。"""
    payload, persisted_loader = await _get_state(
        node_key=RetryService.NODE_WRITING_TENDER_PROCESS,
        target_id=2306,
        latest=None,
        liveness_state="stale",
        document=SimpleNamespace(parsed=0),
        persisted=PersistedFailureSources(vector_job_error="未生成有效文本分块或向量"),
    )
    assert payload["retryable"] is True
    assert payload["failed_node"] == "writing_tender_process"
    assert payload["last_error"] == "未生成有效文本分块或向量"
    persisted_loader.assert_called_once()


@pytest.mark.asyncio
async def test_failed_compose_node_keeps_failure_contract() -> None:
    """真实失败节点（compose TaskRun failed）行为不变: 可重试 + TaskRun 原因。"""
    payload, _ = await _get_state(
        node_key=RetryService.NODE_WRITING_COMPOSE,
        latest=_task_run(
            status=TaskRunStatus.failed.value,
            last_stage="failed",
            last_error=ORPHAN_ERROR,
        ),
        liveness_state="missing",
    )
    assert payload["retryable"] is True
    assert payload["failed_node"] == "writing_compose"
    assert payload["last_error"] == ORPHAN_ERROR


@pytest.mark.asyncio
async def test_never_attempted_node_reports_no_failure() -> None:
    """从未执行的节点（无 TaskRun、心跳 missing）没有可重试的失败对象。"""
    payload, persisted_loader = await _get_state(
        node_key=RetryService.NODE_WRITING_COMPOSE,
        latest=None,
        liveness_state="missing",
    )
    assert payload["retryable"] is False
    assert payload["failed_node"] is None
    assert payload["last_error"] is None
    persisted_loader.assert_not_called()


@pytest.mark.asyncio
async def test_retry_running_node_skips_completion_gate() -> None:
    """显式重试运行中: 门控不得吞掉运行态展示（failed_node 仍指向节点）。"""
    payload, _ = await _get_state(
        node_key=RetryService.NODE_WRITING_EXTRACT,
        latest=_task_run(
            status=TaskRunStatus.running.value,
            last_stage="extracting",
            retry_requested_at=time.time(),
        ),
        liveness_state="live",
        heartbeat={"stage": "extracting", "progress": "40%|提取中"},
    )
    assert payload["retry_running"] is True
    assert payload["failed_node"] == "writing_extract"
