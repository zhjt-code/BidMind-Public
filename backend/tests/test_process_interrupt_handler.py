""" 防御测试: 文档处理任务取消中断的兜底落库(handle_interrupt)。

验证取消(asyncio.CancelledError)路径不写 FAILED, 而是落 LOST 可恢复态,
并在项目已取消时不触碰 DB(避免外键失效)。
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.tasks.process.failure_handler import handle_interrupt
from app.models.vector_job_state import VectorJobStatus


def _noop_heartbeat(*_args, **_kwargs) -> None:
    return None


@pytest.mark.asyncio
async def test_handle_interrupt_writes_lost_state() -> None:
    """取消中断 -> 落 VectorJobStatus.LOST(可恢复), 不写 FAILED。"""
    fake_db = AsyncMock()

    @asynccontextmanager
    async def _fake_factory():
        yield fake_db

    captured: dict = {}

    async def _fake_upsert(_db, **kwargs):
        captured.update(kwargs)

    with patch(
        "app.core.arq_runtime.is_project_cancelled", return_value=False
    ), patch(
        "app.core.tasks.process_task.async_session_factory", _fake_factory
    ), patch(
        "app.core.vectorizer.job_state.upsert_vector_job_state",
        new=AsyncMock(side_effect=_fake_upsert),
    ):
        await handle_interrupt(
            project_id=1478,
            document_id=1249,
            task_name="process_doc_1249",
            queue_mode="arq",
            worker_id="w-1",
            heartbeat=_noop_heartbeat,
        )

    assert captured.get("status") == VectorJobStatus.LOST
    assert captured.get("document_id") == 1249
    fake_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_handle_interrupt_skips_db_when_project_cancelled() -> None:
    """项目已取消/删除 -> 不写任何状态, 避免外键失效(守卫)。"""
    upsert_mock = AsyncMock()

    with patch(
        "app.core.arq_runtime.is_project_cancelled", return_value=True
    ), patch(
        "app.core.vectorizer.job_state.upsert_vector_job_state", new=upsert_mock
    ):
        await handle_interrupt(
            project_id=1478,
            document_id=1249,
            task_name="process_doc_1249",
            queue_mode="arq",
            worker_id="w-1",
            heartbeat=_noop_heartbeat,
        )

    upsert_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_interrupt_swallows_db_error() -> None:
    """中断态落库失败时不得抛出(交由 reconciler 兜底), 保证 CancelledError 能正常 re-raise。"""

    @asynccontextmanager
    async def _broken_factory():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    with patch(
        "app.core.arq_runtime.is_project_cancelled", return_value=False
    ), patch(
        "app.core.tasks.process_task.async_session_factory", _broken_factory
    ):
        # 不应抛出
        await handle_interrupt(
            project_id=1478,
            document_id=1249,
            task_name="process_doc_1249",
            queue_mode="arq",
            worker_id="w-1",
            heartbeat=_noop_heartbeat,
        )
