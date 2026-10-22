""": already_failed 失败原因持久化兜底。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.project import ProjectStatus
from app.models.vector_job_state import VectorJobStatus
from app.services.liveness import document_processing_reconciler as dpr
from app.services.retry.failure_reason_resolver import (
    PersistedFailureSources,
    pick_failure_reason,
)


def test_pick_failure_reason_prefers_heartbeat_then_vector_job():
    reason = pick_failure_reason(
        task_run_error=None,
        heartbeat_error="心跳摘要失败",
        persisted=PersistedFailureSources(
            vector_job_error="文档不可用: PDF所有页面均无文本",
            project_error="项目级失败",
        ),
    )
    assert reason == "心跳摘要失败"

    reason2 = pick_failure_reason(
        task_run_error=None,
        heartbeat_error=None,
        persisted=PersistedFailureSources(
            vector_job_error="文档不可用: PDF所有页面均无文本",
            project_error="项目级失败",
        ),
    )
    assert reason2 is not None
    assert "PDF所有页面均无文本" in reason2


@pytest.mark.asyncio
async def test_resolve_already_failed_message_uses_persisted_sources(monkeypatch):
    async def _fake_load(db, *, project_id, document_id=None):
        assert project_id == 9
        assert document_id == 21
        return PersistedFailureSources(
            vector_job_error="文档不可用: PDF所有页面均无文本、表格与图片，请更换文件后重试",
            project_error=None,
        )

    monkeypatch.setattr(
        "app.services.retry.failure_reason_resolver.load_persisted_failure_sources",
        _fake_load,
    )

    message = await dpr._resolve_already_failed_message(
        SimpleNamespace(),
        project_id=9,
        document_id=21,
        heartbeat_error=None,
    )
    assert "PDF所有页面均无文本" in message


@pytest.mark.asyncio
async def test_reconcile_failed_job_without_heartbeat_returns_persisted_reason(
    monkeypatch,
):
    class _FakeDb:
        def __init__(self):
            self._queues = []

        def queue(self, rows):
            self._queues.append(rows)

        async def execute(self, *_args, **_kwargs):
            rows = self._queues.pop(0) if self._queues else []

            class _Result:
                def scalar_one_or_none(self_inner):
                    return rows[0] if rows else None

            return _Result()

        async def get(self, *_args, **_kwargs):
            return SimpleNamespace(status=ProjectStatus.FAILED, error_message=None)

    db = _FakeDb()
    db.queue([SimpleNamespace(id=21, project_id=9, parsed=0)])
    job_state = SimpleNamespace(
        status=VectorJobStatus.FAILED,
        last_error="文档不可用: PDF所有页面均无文本、表格与图片，请更换文件后重试",
        queue_mode="asyncio",
        arq_job_id=None,
    )

    monkeypatch.setattr(dpr, "get_task_heartbeat", lambda name: None)
    monkeypatch.setattr(dpr, "get_vector_job_state", AsyncMock(return_value=job_state))
    monkeypatch.setattr(
        dpr,
        "_resolve_already_failed_message",
        AsyncMock(
            return_value="文档不可用: PDF所有页面均无文本、表格与图片，请更换文件后重试"
        ),
    )

    result = await dpr.reconcile_document_processing(db, project_id=9, document_id=21)
    assert result.outcome == "already_failed"
    assert "请更换文件后重试" in (result.error_message or "")
