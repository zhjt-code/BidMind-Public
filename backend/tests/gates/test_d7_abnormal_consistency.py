"""Tests for B4.10 D7AbnormalConsistencyGate."""
from __future__ import annotations

import pytest

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.collusion.d7_abnormal_consistency import (
    D7AbnormalConsistencyGate,
    _TYPO_PAIRS,
    _WINDOW_SIZE,
)

# ──────────────────────────────── helpers ─────────────────────────────────────

@pytest.fixture
def gate() -> D7AbnormalConsistencyGate:
    return D7AbnormalConsistencyGate()


def _ctx(
    text_a: str = "",
    text_b: str = "",
    flow: str = "collusion",
    stage: str = "post_llm",
) -> GateContext:
    evidence = {"text_a": text_a, "text_b": text_b}
    return GateContext(flow=flow, stage=stage, target={}, evidence=evidence)  # type: ignore[arg-type]


def _long_text(seed: str, length: int = 600) -> str:
    """重复 seed 直到达到 length 字符。"""
    repeat = (length // len(seed)) + 2
    return (seed * repeat)[:length]


# ──────────────────────────────── applies ─────────────────────────────────────

def test_applies_correct_flow_stage(gate: D7AbnormalConsistencyGate) -> None:
    ctx = _ctx(text_a="abc", text_b="def")
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
    gate: D7AbnormalConsistencyGate, flow: str, stage: str
) -> None:
    ctx = GateContext(flow=flow, stage=stage, target={})  # type: ignore[arg-type]
    assert gate.applies(ctx) is False


# ─────────────────────── evaluate: long identical text → warn ────────────────

def test_evaluate_long_identical_text_warns(gate: D7AbnormalConsistencyGate) -> None:
    """两份文本存在大段相同内容时，应触发 warn。"""
    base = (
        "本公司具有丰富的施工管理经验，项目经理具备一级建造师资格证书，"
        "曾主持多个大型市政工程项目并均按时高质完工，"
        "技术团队拥有专业工程师十余名，设备齐全且均已通过质量认证。"
    )
    # 确保文本足够长以覆盖滑动窗口
    long_text = _long_text(base, length=700)
    ctx = _ctx(text_a=long_text, text_b=long_text)

    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "warn"
    d7 = verdict.suggested_patch["d7_abnormal"]  # type: ignore[index]
    assert d7["score"] >= 0.3
    assert verdict.trace["lcs_signal"] == 0.5  # type: ignore[index]


# ────────────────────────── evaluate: shared typos → warn ────────────────────

def test_evaluate_shared_typos_warn(gate: D7AbnormalConsistencyGate) -> None:
    """两份文本均含相同错别字时，应触发 warn。"""
    # 取第一个错别字对
    correct, wrong = _TYPO_PAIRS[0]  # ("投标", "头标")
    text = f"本公司针对本项目的{wrong}文件已按要求整理完毕，{wrong}人资格符合条件。"
    ctx = _ctx(text_a=text, text_b=text)

    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "warn"
    assert verdict.trace["typo_signal"] > 0  # type: ignore[index]
    assert any(wrong in f for f in verdict.trace["typo_hits"])  # type: ignore[index]


def test_evaluate_multiple_typos_cap_at_0_6(gate: D7AbnormalConsistencyGate) -> None:
    """多个错别字命中时，typo_signal 最高为 0.6。"""
    typo_text = "".join(wrong for _, wrong in _TYPO_PAIRS[:5])
    ctx = _ctx(text_a=typo_text, text_b=typo_text)

    verdict = gate.evaluate(ctx)
    assert verdict.trace["typo_signal"] <= 0.6  # type: ignore[index]


# ──────────────────────── evaluate: short common text → pass ─────────────────

def test_evaluate_short_common_text_passes(gate: D7AbnormalConsistencyGate) -> None:
    """两份文本过短，低于 LCS 窗口下限时不应触发 LCS 信号。"""
    short = "本公司简介如下。"  # 远短于 200 字
    ctx = _ctx(text_a=short, text_b=short)

    verdict = gate.evaluate(ctx)
    assert verdict.trace["lcs_signal"] == 0.0  # type: ignore[index]


# ───────────────────────── evaluate: no typos → lower score ──────────────────

def test_evaluate_no_typos_lower_score(gate: D7AbnormalConsistencyGate) -> None:
    """不含错别字的完全不同文本，d7_score 应为 0。"""
    ctx = _ctx(
        text_a="本公司具有多年建筑施工经验，资质齐全，人员专业。",
        text_b="我方企业在市政领域深耕数十年，技术力量雄厚，设备先进。",
    )
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "pass"
    assert verdict.trace["typo_signal"] == 0.0  # type: ignore[index]


# ────────────────────── evaluate: missing text → pass gracefully ─────────────

@pytest.mark.parametrize(
    "text_a, text_b",
    [
        ("", ""),
        ("", "有内容"),
        ("有内容", ""),
        ("   ", "   "),
    ],
)
def test_evaluate_missing_or_empty_text_passes(
    gate: D7AbnormalConsistencyGate, text_a: str, text_b: str
) -> None:
    ctx = _ctx(text_a=text_a, text_b=text_b)
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "pass"
    assert "不可用" in verdict.reason
    assert verdict.confidence == 0.0


def test_evaluate_no_evidence_key_passes(gate: D7AbnormalConsistencyGate) -> None:
    """evidence 完全为空时也应优雅降级。"""
    ctx = GateContext(flow="collusion", stage="post_llm", target={})
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "pass"


# ─────────────────────── evaluate: patch structure correct ───────────────────

def test_evaluate_patch_structure_correct(gate: D7AbnormalConsistencyGate) -> None:
    """无论 warn 还是 pass，suggested_patch 应符合 d7_abnormal 规范结构。"""
    ctx = _ctx(text_a="abc", text_b="xyz")
    verdict = gate.evaluate(ctx)

    assert verdict.suggested_patch is not None
    d7 = verdict.suggested_patch["d7_abnormal"]
    assert isinstance(d7["score"], float)
    assert isinstance(d7["findings"], list)
    assert d7["evidence_level"] == "second"
    assert d7["certainty"] == 0.8
    assert d7["source"] == "algorithmic"


def test_gate_metadata(gate: D7AbnormalConsistencyGate) -> None:
    assert gate.gate_name == "D7AbnormalConsistencyGate"
    assert gate.flow == "collusion"
    assert gate.stage == "post_llm"
    assert gate.default_severity == "advisory"
    assert gate.order == 70
