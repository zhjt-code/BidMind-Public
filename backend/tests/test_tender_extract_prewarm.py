import inspect
from pathlib import Path

import pytest

from app.core.config import settings


def test_bid_envelope_ready_requires_complete_and_parsed():
    """信封口径: 分卷缺卷或有未解析卷时不得判 bid_parsed(防提前预热)。"""
    from app.core.tasks.process.tender_extract_prewarm import _bid_envelope_ready

    assert _bid_envelope_ready([]) is False
    assert _bid_envelope_ready([(None, 1)]) is True
    assert _bid_envelope_ready([("commercial", 1), ("technical", 1)]) is True
    assert _bid_envelope_ready([("commercial", 1)]) is False
    assert _bid_envelope_ready([("commercial", 1), ("technical", 0)]) is False


def test_prewarm_decision_requires_bid_review_project():
    from app.core.tasks.process.tender_extract_prewarm import (
        TenderExtractPrewarmSnapshot,
        decide_tender_extract_prewarm,
    )

    decision = decide_tender_extract_prewarm(
        TenderExtractPrewarmSnapshot(
            project_exists=True,
            project_type="collusion_check",
            project_status="uploading",
            tender_parsed=True,
            bid_parsed=True,
            requirement_count=0,
            extract_task_active=False,
        )
    )

    assert decision.should_enqueue is False
    assert decision.reason == "not_bid_review_project"


def test_prewarm_decision_requires_both_documents_parsed():
    from app.core.tasks.process.tender_extract_prewarm import (
        TenderExtractPrewarmSnapshot,
        decide_tender_extract_prewarm,
    )

    decision = decide_tender_extract_prewarm(
        TenderExtractPrewarmSnapshot(
            project_exists=True,
            project_type="bid_review",
            project_status="uploading",
            tender_parsed=True,
            bid_parsed=False,
            requirement_count=0,
            extract_task_active=False,
        )
    )

    assert decision.should_enqueue is False
    assert decision.reason == "documents_not_ready"


def test_prewarm_decision_skips_existing_requirements_and_active_task():
    from app.core.tasks.process.tender_extract_prewarm import (
        TenderExtractPrewarmSnapshot,
        decide_tender_extract_prewarm,
    )

    with_requirements = decide_tender_extract_prewarm(
        TenderExtractPrewarmSnapshot(
            project_exists=True,
            project_type="bid_review",
            project_status="uploading",
            tender_parsed=True,
            bid_parsed=True,
            requirement_count=1,
            extract_task_active=False,
        )
    )
    active_task = decide_tender_extract_prewarm(
        TenderExtractPrewarmSnapshot(
            project_exists=True,
            project_type="bid_review",
            project_status="uploading",
            tender_parsed=True,
            bid_parsed=True,
            requirement_count=0,
            extract_task_active=True,
        )
    )

    assert with_requirements.should_enqueue is False
    assert with_requirements.reason == "requirements_exist"
    assert active_task.should_enqueue is False
    assert active_task.reason == "extract_task_active"


def test_prewarm_decision_allows_ready_project():
    from app.core.tasks.process.tender_extract_prewarm import (
        TenderExtractPrewarmSnapshot,
        decide_tender_extract_prewarm,
    )

    decision = decide_tender_extract_prewarm(
        TenderExtractPrewarmSnapshot(
            project_exists=True,
            project_type="bid_review",
            project_status="uploading",
            tender_parsed=True,
            bid_parsed=True,
            requirement_count=0,
            extract_task_active=False,
        )
    )

    assert decision.should_enqueue is True
    assert decision.reason == "ready"


@pytest.mark.asyncio
async def test_maybe_enqueue_prewarm_dispatches_existing_extract_job(monkeypatch):
    from app.core.tasks.process import tender_extract_prewarm
    from app.core.tasks.process.tender_extract_prewarm import TenderExtractPrewarmSnapshot

    async def fake_load_snapshot(project_id, source_document_id):
        assert project_id == 2037
        assert source_document_id == 1642
        return TenderExtractPrewarmSnapshot(
            project_exists=True,
            project_type="bid_review",
            project_status="uploading",
            tender_parsed=True,
            bid_parsed=True,
            requirement_count=0,
            extract_task_active=False,
        )

    calls = []

    async def fake_dispatch_background_job(**kwargs):
        calls.append(kwargs)
        coroutine = kwargs["fallback_factory"]()
        coroutine.close()
        return "arq", "job-123"

    monkeypatch.setattr(settings, "TENDER_EXTRACT_PREWARM_ENABLED", True)
    monkeypatch.setattr(tender_extract_prewarm, "_load_prewarm_snapshot", fake_load_snapshot)
    monkeypatch.setattr(
        tender_extract_prewarm,
        "dispatch_background_job",
        fake_dispatch_background_job,
    )

    decision = await tender_extract_prewarm.maybe_enqueue_tender_extract_prewarm(
        project_id=2037,
        source_document_id=1642,
    )

    assert decision.should_enqueue is True
    assert decision.status == "enqueued"
    assert decision.queue_mode == "arq"
    assert decision.arq_job_id == "job-123"
    assert calls[0]["task_name"] == "extract_project_2037"
    assert calls[0]["arq_function"] == "extract_requirements_job"
    arq_args = calls[0]["arq_args"]
    assert len(arq_args) == 3
    assert arq_args[0] == 2037
    assert isinstance(arq_args[1], str) and len(arq_args[1]) > 0
    assert isinstance(arq_args[2], int) and arq_args[2] >= 1


@pytest.mark.asyncio
async def test_maybe_enqueue_prewarm_respects_config_switch(monkeypatch):
    from app.core.tasks.process import tender_extract_prewarm

    async def fail_if_loaded(project_id, source_document_id):
        raise AssertionError("开关关闭时不应查询预热条件")

    monkeypatch.setattr(settings, "TENDER_EXTRACT_PREWARM_ENABLED", False)
    monkeypatch.setattr(tender_extract_prewarm, "_load_prewarm_snapshot", fail_if_loaded)

    decision = await tender_extract_prewarm.maybe_enqueue_tender_extract_prewarm(
        project_id=2037,
        source_document_id=1642,
    )

    assert decision.should_enqueue is False
    assert decision.reason == "disabled"


# 源码路径按 __file__ 推导，不得依赖工作目录——
# 从仓库根跑 pytest 时相对路径会指向 <repo>/app/... 而非 <repo>/backend/app/...
_PROCESS_TASK_PY = (
    Path(__file__).resolve().parent.parent / "app" / "core" / "tasks" / "process_task.py"
)


def test_process_task_calls_prewarm_after_vectorize_success():
    source = _PROCESS_TASK_PY.read_text(encoding="utf-8")

    assert "maybe_enqueue_tender_extract_prewarm" in source
    assert "extract_prewarm_status" in source
    assert "run_vectorize_stage" in source


def test_prewarm_does_not_call_extract_task_directly_in_process_hook():
    source = _PROCESS_TASK_PY.read_text(encoding="utf-8")
    hook_section = source[source.index("run_vectorize_stage"):]

    assert "run_extract_task(" not in hook_section
    assert "extract_requirements_job" not in hook_section
