"""写标素材显式重试契约测试。"""

from app.models.task_run import TaskRun, TaskRunStatus
from app.schemas.writing import MaterialBatchStatusResponse, WritingRetryStateResponse
from app.services.retry_service import RetryService


def test_task_run_model_exposes_required_retry_columns() -> None:
    columns = set(TaskRun.__table__.columns.keys())

    assert {
        "project_id",
        "flow",
        "node_key",
        "task_name",
        "status",
        "attempt",
        "current_arq_job_id",
        "last_stage",
        "last_error",
        "retry_requested_by",
        "retry_requested_at",
        "preserved_summary_json",
        "reset_scope_json",
    }.issubset(columns)


def test_retry_service_is_scoped_to_writing_material_process() -> None:
    assert RetryService.is_supported_node("writing_material_process")
    assert RetryService.is_supported_node("writing_compose_single")
    assert RetryService.is_supported_node("writing_compose")
    assert RetryService.is_supported_node("writing_match")
    assert RetryService.is_supported_node("writing_outline")
    assert RetryService.is_supported_node("writing_extract")
    assert RetryService.is_supported_node("writing_tender_process")
    assert RetryService.is_supported_node("writing_render")
    assert not RetryService.is_supported_node("unknown_node")
    assert RetryService.FLOW_BID_WRITING == "bid_writing"
    assert RetryService.NODE_WRITING_MATERIAL_PROCESS == "writing_material_process"
    assert RetryService.is_retry_running_status(TaskRunStatus.queued.value)
    assert RetryService.is_retry_running_status(TaskRunStatus.running.value)
    assert not RetryService.is_retry_running_status(TaskRunStatus.completed.value)


def test_retry_contract_lists_preserved_outputs_and_reset_scope() -> None:
    assert "已上传素材文件" in RetryService.WRITING_MATERIAL_PRESERVED_OUTPUTS
    assert "已确认目录" in RetryService.WRITING_MATERIAL_PRESERVED_OUTPUTS
    assert any(
        "processing_status='failed'" in item
        for item in RetryService.WRITING_MATERIAL_RESET_SCOPE
    )
    assert any(
        "heartbeat" in item
        for item in RetryService.WRITING_MATERIAL_RESET_SCOPE
    )


def test_material_process_status_exposes_retry_fields() -> None:
    status = MaterialBatchStatusResponse(
        total=1,
        completed=0,
        failed=1,
        processing=0,
        retryable=True,
        failed_node="writing_material_process",
        failed_stage="image_classify",
        retry_action_label="从素材处理继续重试",
        preserved_outputs=["已上传素材文件"],
        reset_scope=["失败素材"],
        last_attempt=2,
        retry_running=False,
        last_error="boom",
    )

    assert status.retryable is True
    assert status.failed_node == "writing_material_process"
    assert status.target_id is None
    assert status.failed_stage == "image_classify"
    assert status.last_attempt == 2
    assert status.last_error == "boom"


def test_retry_state_response_defaults_are_non_retryable() -> None:
    state = WritingRetryStateResponse()

    assert state.retryable is False
    assert state.failed_node is None
    assert state.target_id is None
    assert state.preserved_outputs == []
    assert state.reset_scope == []
    assert state.last_attempt == 0
    assert state.retry_running is False


def test_retry_contract_registers_remaining_writing_nodes() -> None:
    compose_single = RetryService.NODE_SPECS["writing_compose_single"]
    assert compose_single.requires_target is True
    assert "当前章节上一版草稿" in compose_single.preserved_outputs
    assert "目标章节失败的生成任务状态" in compose_single.reset_scope

    compose = RetryService.NODE_SPECS["writing_compose"]
    assert "已成功章节草稿" in compose.preserved_outputs
    assert "失败或缺失章节草稿" in compose.reset_scope

    match = RetryService.NODE_SPECS["writing_match"]
    assert "已人工确认绑定" in match.preserved_outputs
    assert "未确认的绑定建议" in match.reset_scope

    assert "已人工调整目录" in RetryService.NODE_SPECS["writing_outline"].preserved_outputs
    assert "已人工补录且确认的约束" in RetryService.NODE_SPECS["writing_extract"].preserved_outputs
    assert "失败的 VectorJobState 运行态" in RetryService.NODE_SPECS["writing_tender_process"].reset_scope
    assert RetryService.NODE_SPECS["writing_render"].action_label == "重新渲染文档"


def test_compose_single_task_run_key_is_scoped_by_target() -> None:
    assert (
        RetryService._task_run_key("writing_compose_single", 42)
        == "writing_compose_single:42"
    )
    assert RetryService._public_node_key("writing_compose_single:42") == "writing_compose_single"
    assert RetryService._target_id_from_task_run_key("writing_compose_single:42") == 42
