"""WritingService.get_project_status 状态机分支防御测试基线（R2-5 Phase 1 快照）。

目的: 在 R2-5 把 501 行单方法 ``WritingService.get_project_status`` 按
ReviewService.get_review_status 同款"拆方法不拆类"策略重构前, 固定 12 条
状态机分支的期望输出, 配合现有 ``test_writing_status_recovery.py`` 的 4 条,
合计 16 条覆盖等价 ReviewService 同水平的护栏。

风格沿用 ``test_writing_status_recovery.py``:
``SimpleNamespace + AsyncMock + patch``, 不触碰真实数据库 / Redis / ARQ。

覆盖分支（对齐 R2-5 plan §2.2 Responsibility Inventory 13 条主分支）:

    T-a. UPLOADING + vector_job.status=COMPLETED       -> doc_completed 出口（A1）
    T-b. PARSING  + tender_doc.parsed=1                -> doc_completed 出口（A1）
    T-c. UPLOADING + heartbeat 进行中 + 心跳新鲜        -> vectorizing 进度出口（A4）
    T-d. UPLOADING + decide_recovery=True + recovery<MAX -> dispatch + recovering 写入（A2）
    T-e. PARSING + decide_recovery=True + recovery>=MAX  -> FAILED 出口（A3）
    T-f. EXTRACTING + extract_heartbeat.stage=failed   -> 转 FAILED + retry mark（A6 失败分支）
    T-g. EXTRACTED 无心跳                              -> completed 出口（A7）
    T-h. OUTLINING + liveness.state=stale              -> 转 FAILED + retry + release_binding（B2）
    T-i. MATCHING + liveness.is_terminal + stage=cancelled -> 转 FAILED + heartbeat overlay（B3）
    T-j. WRITING + heartbeat.stage=completed            -> overall_status=completed + progress=100（B5）
    T-k. COMPLETED 项目无 task_name                     -> progress=100 + current_stage=completed（B4）
    T-l. FAILED 项目 + error_message                    -> overall_status=failed + message 透传（B4）
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.core.tasks import TaskLiveness
from app.models.project import ProjectStatus
from app.models.vector_job_state import VectorJobState, VectorJobStatus
from app.services.liveness.document_processing_reconciler import (
    DocumentProcessReconciliationResult,
)
from app.services.writing_service import WritingService

# 上传/解析期恢复决策已收口到共享 reconciler, 写标侧只渲染 outcome
_RECONCILE_PATCH_TARGET = (
    "app.services.liveness.document_processing_reconciler.reconcile_document_processing"
)


def _reconcile_stub(outcome: str, **kwargs) -> AsyncMock:
    return AsyncMock(
        return_value=DocumentProcessReconciliationResult(outcome=outcome, **kwargs)
    )


# ============================================================
# 通用桩 / 工厂
# ============================================================


def _project(
    status: ProjectStatus = ProjectStatus.UPLOADING,
    *,
    project_id: int = 1,
    name: str = "写标项目",
    error_message: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=project_id,
        name=name,
        status=status,
        error_message=error_message,
    )


def _tender_doc(parsed: int = 0, *, document_id: int = 10) -> SimpleNamespace:
    return SimpleNamespace(id=document_id, parsed=parsed)


def _running_arq_state(
    *,
    status: VectorJobStatus = VectorJobStatus.RUNNING,
) -> VectorJobState:
    return VectorJobState(
        project_id=1,
        document_id=10,
        task_name="process_doc_10",
        status=status,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )


def _liveness(
    *,
    state: str = "live",
    heartbeat: dict | None = None,
    stage: str = "",
    age_seconds: float | None = None,
    reason: str = "",
) -> TaskLiveness:
    return TaskLiveness(
        task_name="writing_task_test",
        state=state,
        active=(state == "live"),
        heartbeat=heartbeat,
        stage=stage,
        age_seconds=age_seconds,
        reason=reason,
    )


def _build_service(project: SimpleNamespace, tender: SimpleNamespace | None) -> WritingService:
    db = SimpleNamespace(expire_all=Mock(), commit=AsyncMock(), refresh=AsyncMock())
    service = WritingService(db)  # type: ignore[arg-type]
    service._get_project_or_raise = AsyncMock(return_value=project)  # type: ignore[method-assign]
    service._get_tender_doc = AsyncMock(return_value=tender)  # type: ignore[method-assign]
    return service


# ============================================================
# T-a. UPLOADING + vector_job=COMPLETED -> doc_completed 出口
# ============================================================


@pytest.mark.asyncio
async def test_a_uploading_with_completed_vector_job_returns_doc_completed() -> None:
    project = _project(ProjectStatus.UPLOADING)
    tender = _tender_doc(parsed=0)
    service = _build_service(project, tender)

    completed_state = _running_arq_state(status=VectorJobStatus.COMPLETED)

    with (
        patch("app.services.writing_service.is_task_active", return_value=False),
        patch("app.core.tasks.get_task_heartbeat", side_effect=[None, None]),
        patch("app.core.tasks.get_recovery_count", return_value=0),
        patch("app.core.tasks.reset_recovery_count") as reset_call,
        patch("app.core.vectorizer.job_state.get_vector_job_state", return_value=completed_state),
        patch(_RECONCILE_PATCH_TARGET, _reconcile_stub("noop")),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["current_stage"] == "doc_completed"
    assert result["overall_status"] == "running"
    assert result["overall_progress"] == 50
    assert result["is_recovering"] is False
    reset_call.assert_called_once_with("process_doc_10")


# ============================================================
# T-b. PARSING + tender_doc.parsed=1 -> doc_completed 出口
# ============================================================


@pytest.mark.asyncio
async def test_b_parsing_with_parsed_tender_doc_returns_doc_completed() -> None:
    project = _project(ProjectStatus.PARSING)
    tender = _tender_doc(parsed=1)
    service = _build_service(project, tender)

    with (
        patch("app.services.writing_service.is_task_active", return_value=False),
        patch("app.core.tasks.get_task_heartbeat", side_effect=[None, None]),
        patch("app.core.tasks.get_recovery_count", return_value=0),
        patch("app.core.tasks.reset_recovery_count") as reset_call,
        patch("app.core.vectorizer.job_state.get_vector_job_state", return_value=None),
        patch(_RECONCILE_PATCH_TARGET, _reconcile_stub("completed")),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["current_stage"] == "doc_completed"
    assert result["overall_status"] == "running"
    assert result["overall_progress"] == 50
    assert result["is_recovering"] is False
    reset_call.assert_called_once_with("process_doc_10")


@pytest.mark.asyncio
async def test_parsed_status_returns_doc_completed() -> None:
    """文件齐套后的 parsed 就绪态必须走文档完成出口，而不是继续当上传中。"""
    project = _project(ProjectStatus.PARSED)
    tender = _tender_doc(parsed=1)
    service = _build_service(project, tender)

    with (
        patch("app.services.writing_service.is_task_active", return_value=False),
        patch("app.core.tasks.get_task_heartbeat", side_effect=[None, None]),
        patch("app.core.tasks.get_recovery_count", return_value=0),
        patch("app.core.tasks.reset_recovery_count") as reset_call,
        patch("app.core.vectorizer.job_state.get_vector_job_state", return_value=None),
        patch(_RECONCILE_PATCH_TARGET, _reconcile_stub("noop")),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["status"] == ProjectStatus.PARSED.value
    assert result["current_stage"] == "doc_completed"
    assert result["overall_progress"] == 50
    reset_call.assert_called_once_with("process_doc_10")


# ============================================================
# T-c. UPLOADING + heartbeat 进行中 + 心跳新鲜 -> vectorizing 进度出口
# ============================================================


@pytest.mark.asyncio
async def test_c_uploading_with_fresh_running_heartbeat_returns_running_view() -> None:
    project = _project(ProjectStatus.UPLOADING)
    tender = _tender_doc(parsed=0)
    service = _build_service(project, tender)

    running_hb = {
        "last_heartbeat": time.time(),
        "stage": "vectorizing",
        "progress": "5/10",
        "vl_skipped": False,
    }

    with (
        patch("app.services.writing_service.is_task_active", return_value=True),
        patch("app.core.tasks.get_task_heartbeat", side_effect=[running_hb, None]),
        patch("app.core.tasks.get_recovery_count", return_value=0),
        patch("app.core.vectorizer.job_state.get_vector_job_state", return_value=_running_arq_state()),
        patch(_RECONCILE_PATCH_TARGET, _reconcile_stub("noop")),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["overall_status"] == "running"
    assert result["current_stage"] == "vectorizing"
    assert result["is_recovering"] is False
    assert result["recovery_count"] == 0


# ============================================================
# T-d. UPLOADING + decide_recovery=True + recovery_count=0 -> dispatch + recovering
# ============================================================


@pytest.mark.asyncio
async def test_d_uploading_recovery_delegates_to_reconciler_and_renders_recovering() -> None:
    """: 恢复动作(cancel/enqueue/upsert/计数)全部由共享 reconciler 承担,
 写标侧只负责把 recovered outcome 渲染为 parsing 进行中视图。
 dispatch 细节由 test_liveness_reaper 的 reconciler 用例锁定。
 """
    project = _project(ProjectStatus.UPLOADING)
    tender = _tender_doc(parsed=0)
    service = _build_service(project, tender)

    reconcile = _reconcile_stub("recovered", queue_mode="arq", recovery_count=1)

    with (
        patch("app.services.writing_service.is_task_active", return_value=False),
        patch("app.core.tasks.get_task_heartbeat", side_effect=[
            None,
            None,
            {"stage": "recovering", "progress": "0/0", "vl_skipped": True},
        ]),
        patch("app.core.tasks.get_recovery_count", return_value=1),
        patch("app.core.vectorizer.job_state.get_vector_job_state", return_value=_running_arq_state()),
        patch(_RECONCILE_PATCH_TARGET, reconcile),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    reconcile.assert_awaited_once_with(service.db, project_id=1, document_id=10)
    # 恢复后走 process_heartbeat 进行中分支(stage=parsing, progress="")
    assert result["overall_status"] == "running"
    assert result["current_stage"] == "parsing"
    assert result["is_recovering"] is True
    assert result["recovery_count"] == 1
    assert "图片识别" not in [step["label"] for step in result["steps"]]


# ============================================================
# T-e. PARSING + recovery_count>=MAX_RECOVERY -> FAILED 出口
# ============================================================


@pytest.mark.asyncio
async def test_e_parsing_recovery_exhausted_transitions_to_failed() -> None:
    """max_recovery_exceeded outcome → 失败载荷透传 reconciler 错误文案。

    项目置 FAILED / job_state 终态化由 reconciler 承担, 契约见
    test_reconcile_document_processing_max_recovery_exceeded。
    """
    project = _project(ProjectStatus.PARSING)
    tender = _tender_doc(parsed=0)
    service = _build_service(project, tender)

    err = "任务恢复失败(已重试3次)，请检查服务器环境后重新上传文件"
    reconcile = _reconcile_stub(
        "max_recovery_exceeded", recovery_count=3, error_message=err
    )

    with (
        patch("app.services.writing_service.is_task_active", return_value=False),
        patch("app.core.tasks.get_task_heartbeat", side_effect=[None, None]),
        patch("app.core.tasks.get_recovery_count", return_value=3),
        patch("app.core.vectorizer.job_state.get_vector_job_state", return_value=_running_arq_state()),
        patch(_RECONCILE_PATCH_TARGET, reconcile),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["overall_status"] == "failed"
    assert result["current_stage"] == "failed"
    assert "已重试3次" in result["error_message"]
    assert result["overall_progress"] == 0
    assert result["status"] == "failed"


# ============================================================
# T-f. EXTRACTING + extract_heartbeat.stage=failed -> 转 FAILED + retry mark
# ============================================================


@pytest.mark.asyncio
async def test_f_extracting_heartbeat_failed_transitions_to_failed_and_marks_retry() -> None:
    project = _project(ProjectStatus.EXTRACTING)
    tender = _tender_doc(parsed=1)
    service = _build_service(project, tender)

    extract_hb = {
        "last_heartbeat": time.time(),
        "stage": "failed",
        "progress": "50%|提取异常",
    }
    extract_liveness = _liveness(state="live", heartbeat=extract_hb, stage="failed", age_seconds=0.0)

    with (
        patch("app.services.writing_service.is_task_active", return_value=True),
        patch(
            "app.services.writing_service.inspect_task_liveness",
            return_value=extract_liveness,
        ),
        patch("app.core.tasks.get_task_heartbeat", return_value=None),
        patch("app.core.tasks.get_recovery_count", return_value=0),
        patch(
            "app.services.retry_service.RetryService.mark_task_failed",
            new=AsyncMock(),
        ) as mark_failed,
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert project.status == ProjectStatus.FAILED
    assert result["overall_status"] == "failed"
    assert result["current_stage"] == "failed"
    assert result["error_message"] == "提取异常"
    mark_failed.assert_awaited_once()
    assert mark_failed.call_args.kwargs["node_key"] == "writing_extract"
    assert mark_failed.call_args.kwargs["stage"] == "failed"
    service.db.commit.assert_awaited()


# ============================================================
# T-g. EXTRACTED 无心跳 -> completed 出口
# ============================================================


@pytest.mark.asyncio
async def test_g_extracted_without_heartbeat_returns_completed() -> None:
    project = _project(ProjectStatus.EXTRACTED)
    tender = _tender_doc(parsed=1)
    service = _build_service(project, tender)

    idle_liveness = _liveness(state="idle", heartbeat=None, stage="")

    with (
        patch("app.services.writing_service.is_task_active", return_value=False),
        patch(
            "app.services.writing_service.inspect_task_liveness",
            return_value=idle_liveness,
        ),
        patch("app.core.tasks.get_task_heartbeat", return_value=None),
        patch("app.core.tasks.get_recovery_count", return_value=0),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["overall_status"] == "completed"
    assert result["current_stage"] == "completed"
    assert result["overall_progress"] == 100
    assert result["current_detail"] == "处理完成"


# ============================================================
# T-h. OUTLINING + liveness.state=stale -> 转 FAILED + retry + release_binding
# ============================================================


@pytest.mark.asyncio
async def test_h_outlining_stale_task_transitions_to_failed_and_releases_binding() -> None:
    project = _project(ProjectStatus.OUTLINING)
    service = _build_service(project, tender=None)

    stale_liveness = _liveness(state="stale", heartbeat=None, stage="generating", reason="heartbeat_stale")

    release_calls: list[int] = []

    async def _capture_release(pid: int) -> None:
        release_calls.append(pid)

    with (
        patch(
            "app.services.writing_service.inspect_task_liveness",
            return_value=stale_liveness,
        ),
        patch(
            "app.services.retry_service.RetryService.mark_task_failed",
            new=AsyncMock(),
        ) as mark_failed,
        patch("app.core.ai.release_project_binding", side_effect=_capture_release),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert project.status == ProjectStatus.FAILED
    assert result["overall_status"] == "failed"
    assert result["current_stage"] == "failed"
    assert "请从失败节点继续重试" in result["error_message"]
    mark_failed.assert_awaited_once()
    assert mark_failed.call_args.kwargs["node_key"] == "writing_outline"
    assert release_calls == [1]
    service.db.commit.assert_awaited()
    service.db.refresh.assert_awaited()


# ============================================================
# T-i. MATCHING + liveness.is_terminal + stage=cancelled -> 转 FAILED + overlay
# ============================================================


@pytest.mark.asyncio
async def test_i_matching_terminal_cancelled_transitions_to_failed_with_overlay() -> None:
    project = _project(ProjectStatus.MATCHING)
    service = _build_service(project, tender=None)

    terminal_hb = {"stage": "cancelled", "progress": "30%|用户取消"}
    terminal_liveness = _liveness(
        state="terminal",
        heartbeat=terminal_hb,
        stage="cancelled",
        age_seconds=1.0,
    )

    with (
        patch(
            "app.services.writing_service.inspect_task_liveness",
            return_value=terminal_liveness,
        ),
        patch(
            "app.services.retry_service.RetryService.mark_task_failed",
            new=AsyncMock(),
        ) as mark_failed,
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert project.status == ProjectStatus.FAILED
    assert result["overall_status"] == "failed"
    mark_failed.assert_awaited_once()
    assert mark_failed.call_args.kwargs["node_key"] == "writing_match"
    assert mark_failed.call_args.kwargs["stage"] == "failed"
    service.db.commit.assert_awaited()


# ============================================================
# T-j. WRITING + heartbeat.stage=completed -> completed
# ============================================================


@pytest.mark.asyncio
async def test_j_writing_heartbeat_completed_returns_completed_status() -> None:
    project = _project(ProjectStatus.WRITING)
    service = _build_service(project, tender=None)

    completed_hb = {"stage": "completed", "progress": "100%|内容生成完成"}
    live_liveness = _liveness(
        state="live",
        heartbeat=completed_hb,
        stage="completed",
        age_seconds=0.0,
    )

    with (
        patch(
            "app.services.writing_service.inspect_task_liveness",
            return_value=live_liveness,
        ),
        patch("app.core.tasks.get_recovery_count", return_value=0),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert result["overall_status"] == "completed"
    assert result["current_stage"] == "completed"
    assert result["overall_progress"] == 100
    assert result["current_detail"] == "内容生成完成"


# ============================================================
# T-k. COMPLETED 项目无 task_name -> progress=100 + current_stage=completed
# ============================================================


@pytest.mark.asyncio
async def test_k_completed_project_without_task_returns_terminal_payload() -> None:
    project = _project(ProjectStatus.COMPLETED, name="已完成项目")
    service = _build_service(project, tender=None)

    with (
        patch("app.services.writing_service.inspect_task_liveness") as inspect_call,
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    inspect_call.assert_not_called()
    assert result["overall_status"] == "completed"
    assert result["current_stage"] == "completed"
    assert result["overall_progress"] == 100
    assert result["status"] == "completed"
    assert result["message"] == "处理完成"


# ============================================================
# T-l. FAILED 项目 + error_message 透传
# ============================================================


@pytest.mark.asyncio
async def test_l_failed_project_passes_through_error_message() -> None:
    project = _project(
        ProjectStatus.FAILED,
        name="失败项目",
        error_message="目录生成失败：素材不足",
    )
    service = _build_service(project, tender=None)

    with (
        patch("app.services.writing_service.inspect_task_liveness") as inspect_call,
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    inspect_call.assert_not_called()
    assert result["overall_status"] == "failed"
    assert result["current_stage"] == "failed"
    assert result["current_detail"] == "目录生成失败：素材不足"
    assert result["message"] == "目录生成失败：素材不足"
    assert result["error_message"] == "目录生成失败：素材不足"
