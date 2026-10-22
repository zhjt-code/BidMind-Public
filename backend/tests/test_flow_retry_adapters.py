"""三大流程失败恢复入口的轻量契约测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.routers.collusion import _collusion_retry_payload
from app.models.project import ProjectStatus
from app.models.vector_job_state import VectorJobStatus
from app.services.flow_retry_service import (
    COLLUSION_FLOW_CONFIG,
    COLLUSION_RETRY_SPECS,
    REVIEW_FLOW_CONFIG,
    REVIEW_RETRY_SPECS,
    ReviewRetryContext,
    _build_retry_state,
    build_retry_payload,
    cleanup_review_retry_scope,
    infer_review_retry_context,
)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _ListResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


class _FakeDb:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        if not self.results:
            raise AssertionError(f"unexpected query: {stmt}")
        return self.results.pop(0)


def test_review_retry_payload_exposes_project_level_failure_contract() -> None:
    # S-15: 路由包装函数 _review_retry_payload 已删除，直接测底层构造
    payload = build_retry_payload(
        REVIEW_RETRY_SPECS["review_project"],
        retryable=True,
        last_error="boom",
    )

    assert payload["retryable"] is True
    assert payload["failed_node"] == "review_project"
    assert payload["retry_action_label"] == "重新发起投标审查"
    assert "已确认招标要求" in payload["preserved_outputs"]
    assert "review_project heartbeat" in payload["reset_scope"]
    assert payload["last_error"] == "boom"


def test_review_retry_specs_cover_architecture_chapter_five_nodes() -> None:
    assert {
        "document_process",
        "requirement_extract",
        "bid_response_extract",
        "review_compare",
        "review_report",
    }.issubset(REVIEW_RETRY_SPECS)
    assert REVIEW_RETRY_SPECS["document_process"].action_label == "从文档解析继续重试"
    assert "已有人工补录要求" in REVIEW_RETRY_SPECS["requirement_extract"].preserved_outputs


def test_collusion_retry_payload_exposes_project_level_failure_contract() -> None:
    payload = _collusion_retry_payload(2, "bad")

    assert payload["retryable"] is True
    assert payload["failed_node"] == "collusion_analyze"
    assert payload["retry_action_label"] == "重新发起串标分析"
    assert "已有提取和对比结果" in payload["preserved_outputs"]
    assert "collusion heartbeat" in payload["reset_scope"]
    assert payload["last_error"] == "bad"


def test_review_retry_specs_expose_sub_nodes() -> None:
    assert REVIEW_RETRY_SPECS["bid_response_extract"].action_label == "从投标响应提取继续重试"
    assert "已成功投标响应" in REVIEW_RETRY_SPECS["review_compare"].preserved_outputs
    payload = build_retry_payload(
        REVIEW_RETRY_SPECS["review_compare"],
        retryable=True,
        last_error="missing result",
    )
    assert payload["failed_node"] == "review_compare"
    assert payload["last_error"] == "missing result"


@pytest.mark.asyncio
async def test_infer_review_retry_context_detects_document_process_first() -> None:
    db = _FakeDb([
        _ListResult([SimpleNamespace(id=10, parsed=0)]),
        _ListResult([]),
    ])

    context = await infer_review_retry_context(db, 1)

    assert context.node_key == "document_process"
    assert context.target_id == 10


@pytest.mark.asyncio
async def test_infer_review_retry_context_detects_requirement_extract_before_review() -> None:
    db = _FakeDb([
        _ListResult([
            SimpleNamespace(id=10, parsed=1),
            SimpleNamespace(id=11, parsed=1),
        ]),
        _ListResult([]),
        _ListResult([]),
    ])

    context = await infer_review_retry_context(db, 1)

    assert context.node_key == "requirement_extract"


@pytest.mark.asyncio
async def test_infer_review_retry_context_detects_failed_vector_state() -> None:
    db = _FakeDb([
        _ListResult([SimpleNamespace(id=10, parsed=1)]),
        _ListResult([SimpleNamespace(document_id=10, status=VectorJobStatus.FAILED)]),
    ])

    context = await infer_review_retry_context(db, 1)

    assert context.node_key == "document_process"
    assert context.target_id == 10


@pytest.mark.asyncio
async def test_infer_review_retry_context_detects_bid_response_gap() -> None:
    db = _FakeDb([
        _ListResult([
            SimpleNamespace(id=10, parsed=1),
            SimpleNamespace(id=11, parsed=1),
        ]),
        _ListResult([]),
        _ListResult([101, 102]),
        _ScalarResult(1),
    ])

    context = await infer_review_retry_context(db, 1)

    assert context.node_key == "bid_response_extract"


@pytest.mark.asyncio
async def test_cleanup_requirement_extract_only_deletes_when_no_confirmed_requirements() -> None:
    db = _FakeDb([
        _ScalarResult(0),
        _ScalarResult(None),
    ])

    await cleanup_review_retry_scope(db, 1, "requirement_extract")

    assert any("DELETE FROM requirements" in stmt for stmt in db.statements)
    assert any("requirements.confirmed = :confirmed_1" in stmt for stmt in db.statements)


@pytest.mark.asyncio
async def test_cleanup_requirement_extract_preserves_when_confirmed_exists() -> None:
    db = _FakeDb([_ScalarResult(2)])

    await cleanup_review_retry_scope(db, 1, "requirement_extract")

    assert not any("DELETE FROM requirements" in stmt for stmt in db.statements)


def test_collusion_retry_specs_expose_sub_nodes() -> None:
    assert COLLUSION_RETRY_SPECS["collusion_extract"].action_label == "从文档信息提取继续重试"
    assert "已完成文件对结果" in COLLUSION_RETRY_SPECS["collusion_synthesis"].preserved_outputs
    payload = build_retry_payload(
        COLLUSION_RETRY_SPECS["collusion_compare"],
        retryable=True,
        last_error="pair failed",
    )
    assert payload["failed_node"] == "collusion_compare"
    assert "失败或缺失的文件对结果" in payload["reset_scope"]


def test_review_retry_api_contains_idempotency_and_failed_state_guard() -> None:
    import inspect

    from app.api.routers import reviews

    source = inspect.getsource(reviews.retry_review)

    assert "latest_review_task_run" in source
    assert "is_review_retry_running" in source
    assert "审查重试已在运行" in source
    assert "仅失败状态支持显式重试" in source
    assert "dispatch_background_job" in source
    assert "is_arq_job_possibly_inflight" in source
    inflight_at = source.index("is_arq_job_possibly_inflight")
    assert inflight_at < source.index("cleanup_review_retry_scope")
    assert inflight_at < source.index("dispatch_background_job")


def test_review_compare_cleanup_preserves_completed_results() -> None:
    source = Path("backend/app/services/flow_retry_service.py").read_text(encoding="utf-8")

    assert 'node_key == "review_compare"' in source
    assert 'ReviewResult.failure_state != "completed"' in source


def test_build_retry_payload_returns_byte_equal_fixed_keys() -> None:
    """R2-1 防御：锁定 build_retry_payload 输出 dict 的 10 个字段集与每条 spec 的完整字段值，
 防止 DSL 抽取后 retry-state 字段语义漂移（前端轮询契约依赖； 教训）。"""

    expected_keys = {
        "retryable",
        "failed_node",
        "target_id",
        "failed_stage",
        "retry_action_label",
        "preserved_outputs",
        "reset_scope",
        "last_attempt",
        "retry_running",
        "last_error",
    }

    review_payload = build_retry_payload(
        REVIEW_RETRY_SPECS["review_compare"],
        retryable=True,
        retry_running=False,
        last_error="missing result",
        extra={"target_id": 7, "failed_stage": "review_compare", "last_attempt": 3},
    )
    assert set(review_payload.keys()) == expected_keys
    assert review_payload == {
        "retryable": True,
        "failed_node": "review_compare",
        "target_id": 7,
        "failed_stage": "review_compare",
        "retry_action_label": "从AI对比审查继续重试",
        "preserved_outputs": ["已上传文件", "已确认招标要求", "已成功投标响应"],
        "reset_scope": ["缺失或失败的审查结果", "报告草稿", "review_project heartbeat"],
        "last_attempt": 3,
        "retry_running": False,
        "last_error": "missing result",
    }

    coll_payload = build_retry_payload(
        COLLUSION_RETRY_SPECS["collusion_compare"],
        retryable=False,
        retry_running=True,
        last_error=None,
        extra={"target_id": None, "failed_stage": "comparing", "last_attempt": 1},
    )
    assert set(coll_payload.keys()) == expected_keys
    assert coll_payload == {
        "retryable": False,
        "failed_node": "collusion_compare",
        "target_id": None,
        "failed_stage": "comparing",
        "retry_action_label": "从重点文件对比继续重试",
        "preserved_outputs": ["已上传文件", "成功提取的文档信息", "已成功的文件对结果"],
        "reset_scope": ["失败或缺失的文件对结果", "群组与综合研判结果"],
        "last_attempt": 1,
        "retry_running": True,
        "last_error": None,
    }


def test_flow_retry_config_injection_separates_review_and_collusion() -> None:
    """R2-1 防御：锁定 FlowRetryConfig 在 review / collusion 两侧的差异化注入，
    防止后续横切改动误删 collusion 二级 last_error fallback 或弄混 default_node_key。"""

    assert REVIEW_FLOW_CONFIG.flow_constant == "bid_review"
    assert REVIEW_FLOW_CONFIG.default_node_key == "review_project"
    assert REVIEW_FLOW_CONFIG.retry_specs is REVIEW_RETRY_SPECS
    assert REVIEW_FLOW_CONFIG.extra_last_error_fn is None

    assert COLLUSION_FLOW_CONFIG.flow_constant == "collusion_check"
    assert COLLUSION_FLOW_CONFIG.default_node_key == "collusion_analyze"
    assert COLLUSION_FLOW_CONFIG.retry_specs is COLLUSION_RETRY_SPECS
    assert COLLUSION_FLOW_CONFIG.extra_last_error_fn is not None

    fake_task_run = SimpleNamespace(last_error="boom")
    assert COLLUSION_FLOW_CONFIG.extra_last_error_fn(fake_task_run) == "boom"
    assert COLLUSION_FLOW_CONFIG.extra_last_error_fn(None) is None
    empty_task_run = SimpleNamespace(last_error=None)
    assert COLLUSION_FLOW_CONFIG.extra_last_error_fn(empty_task_run) is None


@pytest.mark.asyncio
async def test_review_retry_state_falls_back_to_project_error_when_running(monkeypatch) -> None:
    """: retry_running 且节点无 last_error 时，回退项目 error_message。"""
    import app.services.flow_retry_service as flow_retry_module

    project = SimpleNamespace(
        id=12,
        status=ProjectStatus.REVIEWING,
        error_message="审查任务心跳超时，请检查 Worker 状态后重新发起审查",
    )
    latest = SimpleNamespace(last_error=None, last_stage="review_compare", attempt=2)

    async def _latest(*_a, **_k):
        return latest

    monkeypatch.setattr(flow_retry_module, "_latest_task_run", _latest)
    monkeypatch.setattr(flow_retry_module, "_is_retry_running", lambda *_a, **_k: True)

    async def _unused_infer(_db, _project):
        raise AssertionError("context 已注入，不应再推断")

    payload = await _build_retry_state(
        None,
        project,
        config=REVIEW_FLOW_CONFIG,
        context=ReviewRetryContext("review_compare"),
        context_cls=ReviewRetryContext,
        infer_context_fn=_unused_infer,
    )

    assert payload["retryable"] is False
    assert payload["retry_running"] is True
    assert payload["last_error"] == project.error_message
