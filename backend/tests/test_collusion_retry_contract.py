"""串标显式重试契约测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.flow_retry_service import (
    COLLUSION_RETRY_SPECS,
    build_retry_payload,
    cleanup_collusion_retry_scope,
    collusion_node_from_stage,
)


class _ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        return _ScalarResult()


def test_collusion_retry_specs_cover_architecture_nodes() -> None:
    assert {
        "document_process",
        "collusion_extract",
        "collusion_global",
        "collusion_compare",
        "collusion_cluster",
        "collusion_synthesis",
        "collusion_report",
        "collusion_analyze",
    }.issubset(COLLUSION_RETRY_SPECS)
    assert COLLUSION_RETRY_SPECS["document_process"].action_label == "从文档解析继续重试"
    assert COLLUSION_RETRY_SPECS["collusion_global"].failed_stage == "global_features"


def test_collusion_retry_payload_exposes_attempt_and_running_fields() -> None:
    payload = build_retry_payload(
        COLLUSION_RETRY_SPECS["collusion_compare"],
        retryable=True,
        retry_running=False,
        last_error="pair failed",
        extra={"last_attempt": 2},
    )

    assert payload["failed_node"] == "collusion_compare"
    assert payload["last_attempt"] == 2
    assert payload["retry_running"] is False
    assert "已成功的文件对结果" in payload["preserved_outputs"]


def test_collusion_stage_mapping_distinguishes_global_and_cluster() -> None:
    assert collusion_node_from_stage("S2") == "collusion_global"
    assert collusion_node_from_stage("global_features") == "collusion_global"
    assert collusion_node_from_stage("S3.5") == "collusion_cluster"
    assert collusion_node_from_stage("S4") == "collusion_synthesis"


@pytest.mark.asyncio
async def test_cleanup_collusion_extract_preserves_success_results() -> None:
    db = _FakeDb()
    project = SimpleNamespace(id=123, collusion_summary={"old": True})

    await cleanup_collusion_retry_scope(db, project, "collusion_extract")

    sql = "\n".join(db.statements)
    assert "DELETE FROM collusion_extractions" in sql
    assert "collusion_extractions.extraction_status != :extraction_status_1" in sql
    assert "DELETE FROM collusion_results" not in sql
    assert "DELETE FROM collusion_groups" not in sql
    assert project.collusion_summary is None


@pytest.mark.asyncio
async def test_cleanup_collusion_synthesis_preserves_groups_and_pairs() -> None:
    db = _FakeDb()
    project = SimpleNamespace(id=123, collusion_summary={"old": True})

    await cleanup_collusion_retry_scope(db, project, "collusion_synthesis")

    sql = "\n".join(db.statements)
    assert "DELETE FROM collusion_results" not in sql
    assert "DELETE FROM collusion_groups" not in sql
    assert project.collusion_summary is None


def test_collusion_retry_api_contains_idempotency_guard() -> None:
    source = Path("backend/app/api/routers/collusion.py").read_text(encoding="utf-8")

    assert "latest_collusion_task_run" in source
    assert "is_collusion_retry_running" in source
    assert "串标重试已在运行" in source
    assert "仅失败状态支持显式重试" in source
    assert "dispatch_background_job" in source


def test_collusion_retry_pre_checks_balance_before_mutating_status() -> None:
    """: 失败后 hold 已释放，显式重试必须先预检余额，禁止先改 REVIEWING 再 403。"""
    router = Path(__file__).resolve().parents[1] / "app" / "api" / "routers" / "collusion.py"
    source = router.read_text(encoding="utf-8")
    retry_fn = source.split("async def retry_collusion", 1)[1].split(
        "async def get_collusion_status", 1
    )[0]
    pre_check_at = retry_fn.find("BillingGuard.pre_check")
    reviewing_at = retry_fn.find("project.status = ProjectStatus.REVIEWING")
    assert pre_check_at != -1, "retry 必须调用 BillingGuard.pre_check"
    assert "InsufficientBalanceError" in retry_fn
    assert reviewing_at != -1
    assert pre_check_at < reviewing_at, "余额预检必须发生在改 REVIEWING 之前"


@pytest.mark.asyncio
async def test_cleanup_collusion_compare_preserves_success_pairs() -> None:
    db = _FakeDb()
    project = SimpleNamespace(id=123, collusion_summary={"old": True})

    await cleanup_collusion_retry_scope(db, project, "collusion_compare")

    sql = "\n".join(db.statements)
    assert "DELETE FROM collusion_results" in sql
    assert "collusion_results.analysis_mode = :analysis_mode_1" in sql
    assert "DELETE FROM collusion_extractions" not in sql
    assert project.collusion_summary is None


def test_collusion_document_process_retry_updates_vector_job_state_only() -> None:
    source = Path("backend/app/services/flow_retry_service.py").read_text(encoding="utf-8")

    assert 'node_key == "document_process"' in source
    assert ".values(parsed=0)" in source
    assert "VectorJobState" in source
    assert "delete(CollusionResult)" not in source.split('if node_key == "document_process":', 1)[1].split('if node_key in {', 1)[0]
