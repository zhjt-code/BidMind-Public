"""stage fail-open 合同: 不适用跳过 / 门禁异常跳过 / 计算失败不外抛; analyzer 降级"""

from types import SimpleNamespace

import pytest

from app.core.document_qa.analyzer import analyze_document
from app.core.tasks.process import document_qa_stage
from tests.document_qa.conftest import make_model


def _noop_heartbeat(*args, **kwargs):
    return None


async def test_stage_skips_non_applicable(monkeypatch):
    async def fake_gate(project_id, document_id):
        return None  # TENDER / 串标 / 写标文档

    monkeypatch.setattr(document_qa_stage, "_load_gate", fake_gate)
    status = await document_qa_stage.run_document_qa_stage(
        parsed_doc=SimpleNamespace(pages=[], page_count=0),
        file_path="x.pdf", project_id=1, document_id=2,
        heartbeat=_noop_heartbeat,
    )
    assert status == "skipped:not_applicable"


async def test_stage_gate_error_fails_open(monkeypatch):
    async def broken_gate(project_id, document_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(document_qa_stage, "_load_gate", broken_gate)
    status = await document_qa_stage.run_document_qa_stage(
        parsed_doc=SimpleNamespace(pages=[], page_count=0),
        file_path="x.pdf", project_id=1, document_id=2,
        heartbeat=_noop_heartbeat,
    )
    assert status == "skipped:gate_error"


async def test_stage_analysis_failure_does_not_raise(monkeypatch):
    async def fake_gate(project_id, document_id):
        return (None, "fh", "ch")

    async def broken_analyze(**kwargs):
        raise RuntimeError("boom")

    marked = {}

    async def fake_mark_failed(project_id, document_id, error):
        marked["error"] = error

    monkeypatch.setattr(document_qa_stage, "_load_gate", fake_gate)
    monkeypatch.setattr(document_qa_stage, "_analyze_and_persist", broken_analyze)
    monkeypatch.setattr(document_qa_stage, "_mark_failed", fake_mark_failed)

    status = await document_qa_stage.run_document_qa_stage(
        parsed_doc=SimpleNamespace(pages=[], page_count=0),
        file_path="x.pdf", project_id=1, document_id=2,
        heartbeat=_noop_heartbeat,
    )
    assert status == "failed"
    assert "boom" in marked["error"]


def test_analyzer_single_checker_failure_degrades(monkeypatch, qa_rules):
    """单检查器抛错 -> checker_status=failed + run 降级, 其余检查器照常产出。"""
    def broken_checker(model, rules):
        raise RuntimeError("checker exploded")

    monkeypatch.setattr(
        "app.core.document_qa.checks.language_check.check_language", broken_checker,
    )
    model = make_model(body_texts={2: "项目经理：XXXX。"})
    analysis = analyze_document(model, qa_rules)
    assert analysis.degraded is True
    assert analysis.checker_status["language"] == "failed"
    assert analysis.checker_status["hygiene"] == "ok"
    assert any(f.rule_code == "DOC-HYG-001" for f in analysis.findings)
    assert analysis.checker_status["layout"] == "skipped"


@pytest.fixture()
def qa_rules():
    from app.core.document_qa.rules.loader import load_rules

    return load_rules()
