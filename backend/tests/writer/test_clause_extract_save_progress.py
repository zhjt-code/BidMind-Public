"""写标约束提取保存段进度心跳契约。

职责: 锁定 catalog/verbatim 落库前必须先报 saving，且 extract 不得提前报 completed。
不做: 不跑真实 LLM/PDF，不测前端投影动画。
"""

from __future__ import annotations

from pathlib import Path

from app.core.writer.clause_extract_progress import build_writing_extract_progress_callback

EXTRACTOR_SRC = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "core"
    / "writer"
    / "clause_extractor.py"
)


def _read_extractor_src() -> str:
    return EXTRACTOR_SRC.read_text(encoding="utf-8")


def _extract_method_body() -> str:
    src = _read_extractor_src()
    start = src.index("    async def extract(")
    end = src.index("# ==================== 渲染配置解析")
    return src[start:end]


def _save_clauses_body() -> str:
    src = _read_extractor_src()
    start = src.index("    async def _save_clauses(")
    end = src.index("    def _build_fixed_template_snapshot(")
    return src[start:end]


def test_save_clauses_reports_saving_before_catalog() -> None:
    """catalog/verbatim 可耗时数十秒，开始前必须先打 saving 心跳，否则右列停在「去重完成」。"""
    body = _save_clauses_body()
    catalog_at = body.index("build_fixed_template_catalog_async")
    prefix = body[:catalog_at]
    assert '_report_progress("saving"' in prefix
    assert "整理固定模板" in prefix


def test_extract_uses_single_pass_instead_of_three_layer_gather() -> None:
    """: extract 不得再对三层 gather _extract_layer。"""
    body = _extract_method_body()
    assert "extract_clause_layers_from_batches" in body
    assert '_extract_layer("hard_constraint"' not in body
    assert '_extract_layer("structure_constraint"' not in body
    assert '_extract_layer("generation_guide"' not in body


def test_extract_does_not_report_completed_after_save() -> None:
    """extract() 报 completed 会被上传进度当成整条流水线结束，评分项/画像会闪成满条完成。"""
    body = _extract_method_body()
    assert '_report_progress("completed"' not in body


def test_saving_start_heartbeat_stays_on_saving_stage_at_seventy_five() -> None:
    hb_calls: list[tuple[str, str]] = []

    def fake_hb(name, *, stage="", progress="", extra=None):  # noqa: ANN001
        hb_calls.append((stage, progress))

    callback = build_writing_extract_progress_callback(
        task_name="writing_extract_3419",
        heartbeat=fake_hb,
    )
    callback("saving", 0, 1, "整理固定模板")

    assert hb_calls[-1] == ("saving", "75%|整理固定模板")
