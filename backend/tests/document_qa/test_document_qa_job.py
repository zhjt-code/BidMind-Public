"""质检以处理队列任务运行: 只在处理 worker 注册、按 job id 去重、许可满时退队不占槽位。

原脱离任务(create_task)测试的覆盖点迁到这里: 不适用文档不进入计算、不写处理进度心跳、
同一文档不叠跑(改由 job id 去重)。夹具含整本卷与分卷技术标两种实体。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from arq.worker import Retry

from app.core.tasks import document_qa_job as job_module


def _names(functions: list[Any]) -> set[str]:
    return {getattr(fn, "name", None) or getattr(fn, "__name__", "") for fn in functions}


# ── 分工: 只由处理 worker 消费 ─────────────────────────────────────────


def test_document_qa_job_registered_only_on_process_worker() -> None:
    from app.core.tasks.resource_policy import resource_type_for_function
    from app.worker import (
        LlmWorkerSettings,
        PROCESS_WORKER_FUNCTION_NAMES,
        ProcessWorkerSettings,
        WorkerSettings,
    )

    assert job_module.DOCUMENT_QA_JOB in _names(ProcessWorkerSettings.functions)
    assert job_module.DOCUMENT_QA_JOB in _names(WorkerSettings.functions)
    assert job_module.DOCUMENT_QA_JOB in PROCESS_WORKER_FUNCTION_NAMES
    assert job_module.DOCUMENT_QA_JOB not in _names(LlmWorkerSettings.functions)
    assert resource_type_for_function(job_module.DOCUMENT_QA_JOB) == "process"


# ── 投递: 固定 job id 去重 ─────────────────────────────────────────────


def _install_enqueue(monkeypatch, *, queued: bool, job_id: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def enqueue(function_name, *args, **kwargs):
        calls.append({"function": function_name, "args": args, "kwargs": kwargs})
        return SimpleNamespace(queued=queued, job_id=job_id, reason="queued" if queued else "no_worker")

    monkeypatch.setattr("app.core.arq_runtime.enqueue_job_detailed", enqueue)
    return calls


async def test_enqueue_uses_fixed_job_id_per_document(monkeypatch) -> None:
    calls = _install_enqueue(monkeypatch, queued=True, job_id="document_qa_2544")

    assert await job_module.enqueue_document_qa(1627, 2544) == "queued"
    assert calls == [{
        "function": "document_qa_job",
        "args": (1627, 2544),
        "kwargs": {"_job_id": "document_qa_2544"},
    }]


async def test_enqueue_reports_duplicate_when_arq_dedupes(monkeypatch) -> None:
    _install_enqueue(monkeypatch, queued=True, job_id="arq-job-unknown")

    assert await job_module.enqueue_document_qa(88, 3002) == "duplicate"


async def test_enqueue_reports_not_queued(monkeypatch) -> None:
    _install_enqueue(monkeypatch, queued=False, job_id="")

    assert await job_module.enqueue_document_qa(88, 3002) == "not_queued:no_worker"


# ── 任务入口: 门禁先于许可, 许可满退队, 预算用尽放行 ─────────────────────


class _Permit:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired
        self.entered = 0

    def __call__(self, *_args: Any, **_kwargs: Any) -> "_Permit":
        self.entered += 1
        return self

    async def __aenter__(self) -> bool:
        return self.acquired

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def _install_job(monkeypatch, *, gate: Any, acquired: bool, cancelled: bool = False) -> tuple[_Permit, list]:
    runs: list[tuple[int, int]] = []
    permit = _Permit(acquired)

    async def is_cancelled(_redis, _project_id):
        return cancelled

    async def fake_gate(_project_id, _document_id):
        return gate

    async def fake_run(project_id, document_id):
        runs.append((project_id, document_id))
        return "completed"

    monkeypatch.setattr("app.core.arq_runtime.is_project_cancelled_async", is_cancelled)
    monkeypatch.setattr("app.core.tasks.process.document_qa_stage._load_gate", fake_gate)
    monkeypatch.setattr("app.core.tasks.resource_permit.permit_context", permit)
    monkeypatch.setattr(job_module, "run_document_qa_for_document", fake_run)
    return permit, runs


async def test_not_applicable_document_skips_before_permit(monkeypatch) -> None:
    """招标文件等不适用文档不占质检许可、不进入计算。"""
    permit, runs = _install_job(monkeypatch, gate=None, acquired=True)

    await job_module.document_qa_job({"redis": None, "job_try": 1}, 1627, 2543)

    assert permit.entered == 0
    assert runs == []


async def test_permit_full_defers_without_running(monkeypatch) -> None:
    permit, runs = _install_job(monkeypatch, gate=(None, "fh", "ch"), acquired=False)

    with pytest.raises(Retry):
        await job_module.document_qa_job({"redis": None, "job_try": 1}, 1627, 2544)

    assert permit.entered == 1
    assert runs == []


async def test_permit_full_after_budget_runs_to_avoid_starvation(monkeypatch) -> None:
    _, runs = _install_job(monkeypatch, gate=("technical", "fh", "ch"), acquired=False)

    await job_module.document_qa_job({"redis": None, "job_try": 99}, 88, 3002)

    assert runs == [(88, 3002)]


async def test_permit_acquired_runs_document(monkeypatch) -> None:
    _, runs = _install_job(monkeypatch, gate=("technical", "fh", "ch"), acquired=True)

    await job_module.document_qa_job({"redis": None, "job_try": 1}, 88, 3002)

    assert runs == [(88, 3002)]


async def test_cancelled_project_skips_everything(monkeypatch) -> None:
    permit, runs = _install_job(monkeypatch, gate=(None, "fh", "ch"), acquired=True, cancelled=True)

    await job_module.document_qa_job({"redis": None, "job_try": 1}, 1627, 2544)

    assert permit.entered == 0
    assert runs == []


# ── 单文档执行: 重解析 -> 质检 -> 收敛; 不写处理进度心跳; 解析失败显形 ─────


class _DocumentSession:
    def __init__(self, document: Any) -> None:
        self.document = document

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.document)


def _install_run(monkeypatch, tmp_path, *, parse_error: bool = False) -> dict[str, Any]:
    pdf = tmp_path / "bid.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    record: dict[str, Any] = {"stage": [], "finalized": [], "failed": [], "beats": []}

    class _Parser:
        def parse(self, path):
            if parse_error:
                raise ValueError("broken pdf")
            return SimpleNamespace(pages=[], page_count=1, path=path)

    async def fake_stage(**kwargs):
        kwargs["heartbeat"]("parsing", "1/1")
        record["stage"].append(kwargs["document_id"])
        return "completed"

    async def fake_finalize(project_id):
        record["finalized"].append(project_id)

    async def fake_mark_failed(project_id, document_id, error):
        record["failed"].append((document_id, error))

    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        lambda: _DocumentSession(SimpleNamespace(file_path=str(pdf))),
    )
    monkeypatch.setattr("app.core.parser.pdf_parser.PdfParser", _Parser)
    monkeypatch.setattr("app.core.tasks.process.document_qa_stage.run_document_qa_stage", fake_stage)
    monkeypatch.setattr("app.core.tasks.process.document_qa_stage._mark_failed", fake_mark_failed)
    monkeypatch.setattr(
        "app.core.tasks.process.document_qa_finalize.maybe_finalize_document_qa_envelope",
        fake_finalize,
    )
    monkeypatch.setattr(
        "app.core.tasks.cancellation.make_project_cancel_check", lambda _pid: (lambda: None),
    )
    monkeypatch.setattr(
        "app.core.tasks.update_heartbeat",
        lambda *args, **kwargs: record["beats"].append(args),
    )
    return record


async def test_run_parses_checks_and_finalizes_without_process_heartbeat(monkeypatch, tmp_path) -> None:
    record = _install_run(monkeypatch, tmp_path)

    status = await job_module.run_document_qa_for_document(1627, 2544)

    assert status == "completed"
    assert record["stage"] == [2544]
    assert record["finalized"] == [1627]
    assert record["beats"] == []


async def test_parse_failure_is_visible_as_failed_run(monkeypatch, tmp_path) -> None:
    record = _install_run(monkeypatch, tmp_path, parse_error=True)

    status = await job_module.run_document_qa_for_document(88, 3002)

    assert status == "failed"
    assert record["stage"] == []
    assert record["failed"][0][0] == 3002
    assert "broken pdf" in record["failed"][0][1]
