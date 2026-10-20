"""RelevanceGuardGate 单元测试（B3.1）。

覆盖：四档 x 3 边界 + relevance=None + is_mandatory 翻转 = 14 tests。
"""

import pytest
from types import SimpleNamespace

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.relevance_guard import RelevanceGuardGate


def _make_ctx(
    relevance_score=50,
    score=80,
    compliance_level="fully",
    risk_level="none",
    is_mandatory=False,
    comment="ok",
    relevance_note="",
    is_compliant=True,
):
    target = SimpleNamespace(
        relevance_score=relevance_score,
        score=score,
        compliance_level=compliance_level,
        risk_level=risk_level,
        is_compliant=is_compliant,
        comment=comment,
        relevance_note=relevance_note,
    )
    evidence = {"is_mandatory": is_mandatory}
    return GateContext(
        flow="investigate",
        stage="post_llm",
        target=target,
        evidence=evidence,
    )


@pytest.fixture
def gate():
    return RelevanceGuardGate()


class TestApplies:
    def test_not_applicable_when_relevance_none(self, gate):
        ctx = _make_ctx(relevance_score=None)
        assert gate.applies(ctx) is False

    def test_not_applicable_when_relevance_ge_80(self, gate):
        ctx = _make_ctx(relevance_score=80)
        assert gate.applies(ctx) is False

    def test_applicable_when_relevance_lt_80(self, gate):
        ctx = _make_ctx(relevance_score=79)
        assert gate.applies(ctx) is True

    def test_not_applicable_wrong_flow(self, gate):
        ctx = GateContext(
            flow="collusion", stage="post_llm",
            target=SimpleNamespace(relevance_score=50),
        )
        assert gate.applies(ctx) is False


class TestGradeCritical:
    """relevance < 30 且 is_mandatory"""

    def test_score_zeroed_mandatory(self, gate):
        ctx = _make_ctx(relevance_score=10, score=90, is_mandatory=True)
        v = gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert v.suggested_patch["score"] == 0
        assert v.suggested_patch["compliance_level"] == "not"
        assert v.suggested_patch["risk_level"] == "high"
        assert v.suggested_patch["is_compliant"] is False

    def test_boundary_29_mandatory(self, gate):
        ctx = _make_ctx(relevance_score=29, is_mandatory=True, score=50)
        v = gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert v.suggested_patch["score"] == 0

    def test_boundary_30_mandatory_falls_to_low(self, gate):
        """30 不进入 critical 档，进入 low 档"""
        ctx = _make_ctx(relevance_score=30, is_mandatory=True, score=50)
        v = gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert v.suggested_patch["score"] != 0  # not zeroed

    def test_below_30_non_mandatory_falls_to_low(self, gate):
        """非否决项即使 < 30 也走 low 档而非 critical"""
        ctx = _make_ctx(relevance_score=20, is_mandatory=False, score=80)
        v = gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert v.suggested_patch.get("score") != 0 or "score" not in v.suggested_patch


class TestGradeLow:
    """relevance < 50"""

    def test_score_capped_above_30(self, gate):
        ctx = _make_ctx(relevance_score=40, score=80, is_mandatory=False)
        v = gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert v.suggested_patch["score"] == min(80, max(40, 20))

    def test_score_not_touched_below_30(self, gate):
        ctx = _make_ctx(relevance_score=40, score=25, is_mandatory=False)
        v = gate.evaluate(ctx)
        assert "score" not in v.suggested_patch or v.suggested_patch.get("score", 25) == 25

    def test_compliance_fully_to_not(self, gate):
        ctx = _make_ctx(relevance_score=45, compliance_level="fully",
                        score=20, is_mandatory=False)
        v = gate.evaluate(ctx)
        assert v.suggested_patch["compliance_level"] == "not"

    def test_risk_none_upgraded_to_medium(self, gate):
        ctx = _make_ctx(relevance_score=45, risk_level="none",
                        score=20, is_mandatory=False)
        v = gate.evaluate(ctx)
        assert v.suggested_patch["risk_level"] == "medium"

    def test_mandatory_forces_high_and_non_compliant(self, gate):
        ctx = _make_ctx(relevance_score=40, is_mandatory=True,
                        score=80, risk_level="low")
        v = gate.evaluate(ctx)
        assert v.suggested_patch["risk_level"] == "high"
        assert v.suggested_patch["is_compliant"] is False

    def test_boundary_49(self, gate):
        ctx = _make_ctx(relevance_score=49, score=80, is_mandatory=False)
        v = gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"


class TestGradeMedium:
    """50 <= relevance < 80"""

    def test_score_capped_75(self, gate):
        ctx = _make_ctx(relevance_score=60, score=90)
        v = gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert v.suggested_patch["score"] == 75

    def test_compliance_fully_to_partially(self, gate):
        ctx = _make_ctx(relevance_score=70, score=50, compliance_level="fully")
        v = gate.evaluate(ctx)
        assert v.suggested_patch["compliance_level"] == "partially"

    def test_risk_none_to_low(self, gate):
        ctx = _make_ctx(relevance_score=60, score=50, risk_level="none",
                        compliance_level="partially")
        v = gate.evaluate(ctx)
        assert v.suggested_patch["risk_level"] == "low"

    def test_no_patch_when_nothing_to_change(self, gate):
        """score <= 75, compliance != fully, risk != none → pass"""
        ctx = _make_ctx(relevance_score=60, score=70,
                        compliance_level="partially", risk_level="low")
        v = gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_boundary_50(self, gate):
        ctx = _make_ctx(relevance_score=50, score=80)
        v = gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert v.suggested_patch["score"] == 75

    def test_boundary_79(self, gate):
        ctx = _make_ctx(relevance_score=79, score=90)
        v = gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert v.suggested_patch["score"] == 75
