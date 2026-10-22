def test_missing_file_error_is_unrecoverable_document_error() -> None:
    from app.services import flow_retry_service

    assert hasattr(flow_retry_service, "is_unrecoverable_document_error")
    assert flow_retry_service.is_unrecoverable_document_error(
        "文件不存在: storage/uploads/67/bid_x.pdf"
    ) is True
    assert flow_retry_service.is_unrecoverable_document_error(
        "No such file or directory: storage/uploads/67/bid_x.pdf"
    ) is True


def test_unexpected_eof_error_is_unrecoverable_document_error() -> None:
    from app.services import flow_retry_service

    assert flow_retry_service.is_unrecoverable_document_error("Unexpected EOF") is True
    assert flow_retry_service.is_unrecoverable_document_error("EOF marker not found") is True


def test_llm_timeout_remains_recoverable() -> None:
    from app.services import flow_retry_service

    assert flow_retry_service.is_unrecoverable_document_error("串标分析任务心跳超时") is False
    assert flow_retry_service.is_unrecoverable_document_error("多路提取超时（180秒）") is False


def test_unrecoverable_document_payload_points_to_reupload() -> None:
    from app.services import flow_retry_service

    assert hasattr(flow_retry_service, "build_unrecoverable_document_retry_payload")
    payload = flow_retry_service.build_unrecoverable_document_retry_payload(
        flow_retry_service.COLLUSION_RETRY_SPECS["document_process"],
        target_id=1813,
        last_error="Unexpected EOF",
    )

    assert payload["retryable"] is False
    assert payload["target_id"] == 1813
    assert payload["retry_action_label"] == "重新上传文件"
    assert payload["last_error"] == "Unexpected EOF"
    assert "源文件不可读" in payload["reset_scope"]


import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_review_document_process_missing_file_blocks_retry() -> None:
    """: 投标审查 document_process 节点遇「文件不存在」也必须阻断重试并引导重传。

 此前不可恢复文件闸只接入串标, 投标审查同节点缺同款保护。
 """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models.project import ProjectStatus
    from app.services import flow_retry_service
    from app.services.flow_retry_service import (
        REVIEW_FLOW_CONFIG,
        ReviewRetryContext,
        _build_retry_state,
    )

    latest_run = SimpleNamespace(
        node_key="document_process",
        last_error="文件不存在: storage/uploads/1524/tender_招标文件-0521发.pdf",
        last_stage="processing",
        retry_requested_at=None,
        status="failed",
        attempt=1,
    )

    class _FakeResult:
        def scalar_one_or_none(self):
            return latest_run

    class _FakeDb:
        async def execute(self, _stmt):
            return _FakeResult()

    project = SimpleNamespace(
        id=1524, status=ProjectStatus.FAILED, error_message=None
    )

    payload = await _build_retry_state(
        _FakeDb(),  # type: ignore[arg-type]
        project,  # type: ignore[arg-type]
        config=REVIEW_FLOW_CONFIG,
        context=ReviewRetryContext("document_process", target_id=2337),
        context_cls=ReviewRetryContext,
        infer_context_fn=AsyncMock(),
    )

    assert payload["retryable"] is False
    assert payload["retry_action_label"] == "重新上传文件"
    assert payload["target_id"] == 2337
    assert "源文件不可读" in payload["reset_scope"]
    # 同款错误在串标侧行为不变(既有 契约)
    assert flow_retry_service.is_unrecoverable_document_error(
        latest_run.last_error
    ) is True
