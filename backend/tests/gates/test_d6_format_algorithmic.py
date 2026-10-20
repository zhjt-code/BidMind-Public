"""Tests for B4.9 D6FormatAlgorithmicGate."""
from __future__ import annotations

import pytest

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.collusion.d6_format_algorithmic import D6FormatAlgorithmicGate

# ──────────────────────────────── fixtures ────────────────────────────────────

@pytest.fixture
def gate() -> D6FormatAlgorithmicGate:
    return D6FormatAlgorithmicGate()


def _struct(
    toc: list[str] | None = None,
    fonts: list[str] | None = None,
    margins: dict | None = None,
    hf: list[str] | None = None,
) -> dict:
    return {
        "toc_headings": toc or [],
        "font_families": fonts or [],
        "margins": margins or {},
        "headers_footers": hf or [],
    }


def _ctx(
    struct_a: dict | None = None,
    struct_b: dict | None = None,
    whitelist: dict | None = None,
    flow: str = "collusion",
    stage: str = "post_llm",
) -> GateContext:
    evidence: dict = {}
    if struct_a is not None:
        evidence["docx_structure_a"] = struct_a
    if struct_b is not None:
        evidence["docx_structure_b"] = struct_b
    if whitelist is not None:
        evidence["tender_format_whitelist"] = whitelist
    return GateContext(flow=flow, stage=stage, target={}, evidence=evidence)  # type: ignore[arg-type]


# ──────────────────────────────── applies ─────────────────────────────────────

def test_applies_correct_flow_stage(gate: D6FormatAlgorithmicGate) -> None:
    ctx = _ctx(struct_a=_struct(), struct_b=_struct())
    assert gate.applies(ctx) is True


@pytest.mark.parametrize(
    "flow, stage",
    [
        ("investigate", "post_llm"),
        ("writer", "post_llm"),
        ("collusion", "pre_llm"),
        ("collusion", "post_pipeline"),
        ("all", "post_llm"),
    ],
)
def test_applies_wrong_flow_or_stage(
    gate: D6FormatAlgorithmicGate, flow: str, stage: str
) -> None:
    ctx = GateContext(flow=flow, stage=stage, target={})  # type: ignore[arg-type]
    assert gate.applies(ctx) is False


# ──────────────────────────── evaluate: high similarity → warn ─────────────────

def test_evaluate_high_similarity_warns(gate: D6FormatAlgorithmicGate) -> None:
    """完全相同的结构（超白名单）应触发 warn。"""
    toc = ["第一章 总则", "第二章 投标人资格", "第三章 投标文件", "第四章 评标办法", "第五章 合同条款"]
    fonts = ["宋体", "黑体"]
    margins = {"top": 25.4, "bottom": 25.4, "left": 31.8, "right": 25.4}
    hf = ["某公司投标文件", "第 1 页"]

    struct = _struct(toc=toc, fonts=fonts, margins=margins, hf=hf)
    ctx = _ctx(struct_a=struct, struct_b=struct, whitelist={})

    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "warn"
    assert verdict.suggested_patch is not None
    d6 = verdict.suggested_patch["d6_format"]
    assert d6["score"] >= 0.5
    assert d6["source"] == "algorithmic"


# ──────────────────────────── evaluate: low similarity → pass ──────────────────

def test_evaluate_low_similarity_passes(gate: D6FormatAlgorithmicGate) -> None:
    struct_a = _struct(
        toc=["第一章 总则", "第二章 资格"],
        fonts=["宋体"],
        margins={"top": 25.4, "bottom": 25.4, "left": 31.8, "right": 25.4},
    )
    struct_b = _struct(
        toc=["Chapter 1 Intro", "Chapter 2 Background"],
        fonts=["Arial", "Times New Roman"],
        margins={"top": 20.0, "bottom": 20.0, "left": 20.0, "right": 20.0},
    )
    ctx = _ctx(struct_a=struct_a, struct_b=struct_b)

    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "pass"
    d6 = verdict.suggested_patch["d6_format"]  # type: ignore[index]
    assert d6["score"] < 0.5


# ──────────────────────── evaluate: whitelist deduction ──────────────────────

def test_evaluate_whitelist_deducts_font_signal(gate: D6FormatAlgorithmicGate) -> None:
    """字体和边距均在白名单内时，信号应被扣除，不应触发 warn。"""
    fonts = ["宋体", "黑体"]
    margins = {"top": 25.4, "bottom": 25.4, "left": 31.8, "right": 25.4}

    struct = _struct(fonts=fonts, margins=margins)
    whitelist = {
        "required_fonts": ["宋体", "黑体"],
        "required_margins": margins,
    }
    ctx = _ctx(struct_a=struct, struct_b=struct, whitelist=whitelist)

    verdict = gate.evaluate(ctx)
    d6 = verdict.suggested_patch["d6_format"]  # type: ignore[index]
    # font_signal 和 margin_signal 均应被扣除
    assert verdict.trace["font_signal"] == 0.0  # type: ignore[index]
    assert verdict.trace["margin_signal"] == 0.0  # type: ignore[index]
    # 总分应低于仅看字体+边距的 0.5
    assert d6["score"] < 0.5


def test_evaluate_whitelist_partial_font_not_superset(gate: D6FormatAlgorithmicGate) -> None:
    """白名单字体未完全覆盖实际字体时，不扣除。"""
    fonts = ["宋体", "黑体", "楷体"]
    struct = _struct(fonts=fonts)
    whitelist = {"required_fonts": ["宋体"]}  # 不是超集
    ctx = _ctx(struct_a=struct, struct_b=struct, whitelist=whitelist)

    verdict = gate.evaluate(ctx)
    assert verdict.trace["font_signal"] == 0.25  # type: ignore[index]


# ──────────────────────── evaluate: missing evidence → pass ──────────────────

def test_evaluate_missing_both_structures_passes(gate: D6FormatAlgorithmicGate) -> None:
    ctx = _ctx()
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "pass"
    assert "不可用" in verdict.reason


def test_evaluate_missing_one_structure_passes(gate: D6FormatAlgorithmicGate) -> None:
    ctx = _ctx(struct_a=_struct(toc=["第一章"]))
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "pass"
    assert "不可用" in verdict.reason


# ──────────────────────── evaluate: patch structure ──────────────────────────

def test_evaluate_patch_structure_correct(gate: D6FormatAlgorithmicGate) -> None:
    """suggested_patch 应符合 d6_format 规范结构。

 后契约: ``DIMENSION_EVIDENCE_LEVEL_CONTRACT["d6_format"] == "third"``,
 Gate 直接产出契约值, 不再依赖 ``_normalize_dim_evidence_levels`` 事后兜底。
 """
    struct = _struct(fonts=["宋体"])
    ctx = _ctx(struct_a=struct, struct_b=struct)

    verdict = gate.evaluate(ctx)
    assert verdict.suggested_patch is not None
    d6 = verdict.suggested_patch["d6_format"]

    assert isinstance(d6["score"], float)
    assert isinstance(d6["findings"], list)
    assert d6["evidence_level"] == "third"
    assert d6["certainty"] == 0.9
    assert d6["source"] == "algorithmic"


def test_gate_metadata(gate: D6FormatAlgorithmicGate) -> None:
    assert gate.gate_name == "D6FormatAlgorithmicGate"
    assert gate.flow == "collusion"
    assert gate.stage == "post_llm"
    assert gate.default_severity == "advisory"
    assert gate.order == 60
