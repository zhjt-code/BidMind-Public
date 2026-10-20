"""写标上传阶段恢复判定防御测试。"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.project import ProjectStatus
from app.models.vector_job_state import VectorJobState, VectorJobStatus
from app.core.tasks import cleanup_task, update_heartbeat
from app.core.tasks.writing_extract_task import get_writing_extract_task_name
from app.services.liveness.document_processing_reconciler import (
    DocumentProcessReconciliationResult,
)
from app.services.writing_service import WritingService

_RECONCILE_PATCH_TARGET = (
    "app.services.liveness.document_processing_reconciler.reconcile_document_processing"
)


def _project(status: ProjectStatus = ProjectStatus.UPLOADING) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        name="写标项目",
        status=status,
        error_message=None,
    )


def _tender_doc(parsed: int = 0) -> SimpleNamespace:
    return SimpleNamespace(id=10, parsed=parsed)


def _running_arq_state() -> VectorJobState:
    return VectorJobState(
        project_id=1,
        document_id=10,
        task_name="process_doc_10",
        status=VectorJobStatus.RUNNING,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )


@pytest.mark.asyncio
async def test_writing_upload_status_delegates_recovery_to_shared_reconciler() -> None:
    """: 写标上传状态页必须委托共享 reconciler, noop 时按心跳渲染进行中视图。

 恢复判定细节(Redis 心跳新鲜度/ARQ 在途守卫等)由 reconciler 单元测试锁定,
 本用例只锁定「委托 + noop 渲染」契约。
 """
    db = SimpleNamespace(expire_all=Mock(), commit=AsyncMock())
    service = WritingService(db)  # type: ignore[arg-type]
    service._get_project_or_raise = AsyncMock(return_value=_project())  # type: ignore[method-assign]
    service._get_tender_doc = AsyncMock(return_value=_tender_doc(parsed=0))  # type: ignore[method-assign]

    heartbeat = {
        "last_heartbeat": time.time(),
        "stage": "vectorizing",
        "progress": "1/10",
        "vl_skipped": False,
    }
    reconcile = AsyncMock(
        return_value=DocumentProcessReconciliationResult(outcome="noop")
    )

    with (
        patch("app.services.writing_service.is_task_active", return_value=True),
        patch("app.core.tasks.get_task_heartbeat", side_effect=[heartbeat, None]),
        patch("app.core.tasks.get_recovery_count", return_value=0),
        patch("app.core.vectorizer.job_state.get_vector_job_state", return_value=_running_arq_state()),
        patch(_RECONCILE_PATCH_TARGET, reconcile),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["overall_status"] == "running"
    assert result["current_stage"] == "vectorizing"
    reconcile.assert_awaited_once_with(db, project_id=1, document_id=10)
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_writing_upload_status_prefers_parsed_document_over_stale_recovery_heartbeat() -> None:
    """文档已解析完成时, 残留 recovering 心跳不能把写标上传页拖回恢复中。"""
    db = SimpleNamespace(expire_all=Mock(), commit=AsyncMock())
    service = WritingService(db)  # type: ignore[arg-type]
    service._get_project_or_raise = AsyncMock(return_value=_project())  # type: ignore[method-assign]
    service._get_tender_doc = AsyncMock(return_value=_tender_doc(parsed=1))  # type: ignore[method-assign]

    heartbeat = {
        "last_heartbeat": time.time(),
        "stage": "recovering",
        "progress": "",
    }
    reconcile = AsyncMock(
        return_value=DocumentProcessReconciliationResult(outcome="completed")
    )

    with (
        patch("app.services.writing_service.is_task_active", return_value=True),
        patch("app.core.tasks.get_task_heartbeat", side_effect=[heartbeat, None]),
        patch("app.core.tasks.get_recovery_count", return_value=1),
        patch("app.core.tasks.reset_recovery_count") as reset_recovery_count,
        patch("app.core.vectorizer.job_state.get_vector_job_state", return_value=_running_arq_state()),
        patch(_RECONCILE_PATCH_TARGET, reconcile),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["overall_status"] == "running"
    assert result["current_stage"] == "doc_completed"
    assert result["is_recovering"] is False
    reset_recovery_count.assert_called_once_with("process_doc_10")
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_writing_upload_renders_failed_payload_for_already_failed_without_heartbeat() -> None:
    """: reconciler 判 already_failed(如源文件缺失终态化)且心跳已清理时,
 写标上传页必须渲染失败载荷并透传重传引导文案。

 原「恢复分支二次确认竞态」意图已由 reconciler 的
 test_reconcile_document_processing_completed_race 等价覆盖(原则)。
 """
    db = SimpleNamespace(expire_all=Mock(), commit=AsyncMock())
    service = WritingService(db)  # type: ignore[arg-type]
    service._get_project_or_raise = AsyncMock(return_value=_project(ProjectStatus.PARSING))  # type: ignore[method-assign]
    service._get_tender_doc = AsyncMock(return_value=_tender_doc(parsed=0))  # type: ignore[method-assign]

    err = (
        "文件不存在: storage/uploads/1/tender_x.pdf，"
        "源文件缺失，重试无法恢复，请重新上传后再解析"
    )
    reconcile = AsyncMock(
        return_value=DocumentProcessReconciliationResult(
            outcome="already_failed", error_message=err
        )
    )

    with (
        patch("app.services.writing_service.is_task_active", return_value=False),
        patch("app.core.tasks.get_task_heartbeat", side_effect=[None, None]),
        patch("app.core.tasks.get_recovery_count", return_value=0),
        patch("app.core.vectorizer.job_state.get_vector_job_state", return_value=None),
        patch(_RECONCILE_PATCH_TARGET, reconcile),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["overall_status"] == "failed"
    assert result["current_stage"] == "failed"
    assert "请重新上传" in (result["error_message"] or "")
    assert result["steps"] == []
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_writing_extract_stale_heartbeat_returns_failed_view_without_project_terminal_write() -> None:
    """约束提取 Worker 已失活时, 返回失败视图但不由状态查询写项目终态。"""
    db = SimpleNamespace(expire_all=Mock(), commit=AsyncMock(), refresh=AsyncMock())
    service = WritingService(db)  # type: ignore[arg-type]
    project = _project(ProjectStatus.EXTRACTING)
    service._get_project_or_raise = AsyncMock(return_value=project)  # type: ignore[method-assign]
    service._get_tender_doc = AsyncMock(return_value=_tender_doc(parsed=1))  # type: ignore[method-assign]

    task_name = get_writing_extract_task_name(1)
    cleanup_task(task_name)
    update_heartbeat(
        task_name,
        stage="extracting_generation_guide",
        progress="57%|生成指引 32/56",
        extra={
            "queue_mode": "arq",
            "last_heartbeat": time.time() - 600,
        },
    )

    with patch("app.services.retry_service.RetryService.mark_task_failed", new=AsyncMock()) as mark_failed:
        try:
            result = await service.get_project_status(project_id=1, user=SimpleNamespace())
        finally:
            cleanup_task(task_name)

    assert result["overall_status"] == "failed"
    assert result["current_stage"] == "failed"
    assert "约束提取任务心跳超时" in result["error_message"]
    assert project.status == ProjectStatus.EXTRACTING
    mark_failed.assert_awaited_once()
    assert mark_failed.call_args.kwargs["node_key"] == "writing_extract"
    db.commit.assert_not_awaited()
