"""CrossDimensionHintGate 单元测试（B3.4）。"""

import pytest
from app.core.ai.gates.base import GateContext
from app.core.ai.gates.collusion.cross_dimension_hint import CrossDimensionHintGate


def _ctx(target):
    return GateContext(flow="collusion", stage="post_pipeline", target=target)


@pytest.fixture
def gate():
    return CrossDimensionHintGate()


def test_not_applicable_wrong_stage(gate):
    ctx = GateContext(flow="collusion", stage="post_llm", target={})
    assert gate.applies(ctx) is False


def test_d1_high_d2_zero_adds_hint(gate):
    target = {
        "d1_personnel": {"score": 0.8},
        "d2_company": {"score": 0, "findings": []},
    }
    v = gate.evaluate(_ctx(target))
    assert v.verdict == "warn"
    assert len(target["d2_company"]["findings"]) == 1
    assert "关联提示" in target["d2_company"]["findings"][0]["summary"]


def test_d1_high_d2_missing_creates_d2(gate):
    target = {"d1_personnel": {"score": 0.6}}
    gate.evaluate(_ctx(target))
    assert "d2_company" in target
    assert len(target["d2_company"]["findings"]) == 1


def test_d3_high_d7_zero_adds_hint(gate):
    target = {
        "d3_text": {"score": 0.9},
        "d7_abnormal": {"score": 0, "findings": []},
    }
    v = gate.evaluate(_ctx(target))
    assert v.verdict == "warn"
    assert len(target["d7_abnormal"]["findings"]) == 1


def test_d1_low_no_hint(gate):
    target = {
        "d1_personnel": {"score": 0.3},
        "d2_company": {"score": 0, "findings": []},
    }
    v = gate.evaluate(_ctx(target))
    assert v.verdict == "pass"
    assert len(target["d2_company"]["findings"]) == 0


def test_d2_already_has_score_no_hint(gate):
    target = {
        "d1_personnel": {"score": 0.8},
        "d2_company": {"score": 0.5, "findings": []},
    }
    v = gate.evaluate(_ctx(target))
    assert v.verdict == "pass"


def test_both_hints_added(gate):
    target = {
        "d1_personnel": {"score": 0.8},
        "d3_text": {"score": 0.7},
    }
    v = gate.evaluate(_ctx(target))
    assert v.verdict == "warn"
    assert "d2_company" in target
    assert "d7_abnormal" in target
