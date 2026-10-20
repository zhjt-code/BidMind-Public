""" 防御测试: 招标要求提取被 ARQ 取消时必须显式落失败态。

职责: 锁定 run_extract_task 的 CancelledError 收尾契约。
不做: 不启动真实 ARQ、不连接真实数据库、不调用真实 LLM。
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks import extract_task as extract_task_module  # noqa: E402
from app.models.project import ProjectStatus  # noqa: E402


class _FakeDb:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _CancellingExtractor:
    _stage_failures: list[dict] = []

    async def extract_from_document(
        self, *, document_id: int, save_to_db: bool, cancel_check=None
    ) -> list:
        raise asyncio.CancelledError("arq timeout")


@pytest.mark.asyncio
async def test_run_extract_task_cancelled_error_marks_project_failed(monkeypatch):
    """ARQ 超时取消不是业务取消时，应写 FAILED/失败心跳/TaskRun 后重新抛出。"""
    project = SimpleNamespace(
        id=1389,
        status=ProjectStatus.EXTRACTING,
        error_message=None,
        extraction_warnings=None,
    )
    tender_doc = SimpleNamespace(id=2137)
    fake_db = _FakeDb()
    heartbeats: list[tuple[str, str]] = []
    mark_failed = AsyncMock()

    @asynccontextmanager
    async def _fake_session_factory():
        yield fake_db

    monkeypatch.setattr(extract_task_module, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(extract_task_module, "_get_project", AsyncMock(return_value=project))
    monkeypatch.setattr(
        extract_task_module,
        "_get_tender_document",
        AsyncMock(return_value=tender_doc),
    )
    monkeypatch.setattr(
        extract_task_module,
        "get_tender_extractor",
        lambda **_kwargs: _CancellingExtractor(),
    )
    monkeypatch.setattr(
        "app.core.tasks.update_heartbeat",
        lambda _task_name, stage="", progress="", **_kwargs: heartbeats.append(
            (stage, progress)
        ),
    )
    monkeypatch.setattr(
        "app.core.arq_runtime.is_project_cancelled",
        lambda _project_id: False,
    )
    monkeypatch.setattr(
        "app.core.tasks.cancellation.make_project_cancel_check",
        lambda _project_id: (lambda: None),
    )
    monkeypatch.setattr(
        "app.services.flow_retry_service.mark_review_task_run_by_task_name",
        mark_failed,
    )

    with pytest.raises(asyncio.CancelledError):
        await extract_task_module.run_extract_task(project_id=1389)

    assert project.status == ProjectStatus.FAILED
    assert project.error_message and "任务被终止" in project.error_message
    assert any(stage == "失败" for stage, _progress in heartbeats)
    assert fake_db.commits == 1
    mark_failed.assert_awaited_once()
