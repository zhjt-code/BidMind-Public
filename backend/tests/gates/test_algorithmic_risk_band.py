"""Unit tests for AlgorithmicRiskBandGate.

derive_risk_band and the prometheus metrics counters are mocked so the suite
is fully isolated from the algorithm logic and monitoring infrastructure.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.algorithmic_risk_band import AlgorithmicRiskBandGate
from app.core.ai.risk_band import RiskBandVerdict

# ---------------------------------------------------------------------------
# Module-level paths for patching
# ---------------------------------------------------------------------------

_DERIVE_PATH = "app.core.ai.risk_band.derive_risk_band"
_METRICS_MOD = "app.core.observability.metrics"

GATE = AlgorithmicRiskBandGate()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rb_verdict(band: str, confidence: float = 0.9) -> RiskBandVerdict:
    return RiskBandVerdict(
        risk_band=band,  # type: ignore[arg-type]
        confidence=confidence,
        contributing_factors={"triggers": ["test_trigger"]},
    )


def _ctx(
    flow: str = "investigate",
    stage: str = "post_llm",
    llm_band: str = "medium",
    evidence: dict | None = None,
) -> GateContext:
    target = SimpleNamespace(risk_level=llm_band)
    return GateContext(
        flow=flow,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        target=target,
        evidence=evidence or {},
    )


def _make_counter_mock() -> MagicMock:
    """Return a mock that supports .labels(...).inc()."""
    mock = MagicMock()
    mock.labels.return_value = mock
    return mock


# ---------------------------------------------------------------------------
# applies() tests
# ---------------------------------------------------------------------------

class TestApplies:
    def test_applies_true_when_investigate_post_llm(self) -> None:
        assert GATE.applies(_ctx(flow="investigate", stage="post_llm")) is True

    def test_applies_false_when_flow_is_collusion(self) -> None:
        assert GATE.applies(_ctx(flow="collusion", stage="post_llm")) is False

    def test_applies_false_when_flow_is_writer(self) -> None:
        assert GATE.applies(_ctx(flow="writer", stage="post_llm")) is False

    def test_applies_false_when_stage_is_pre_llm(self) -> None:
        ctx = GateContext(flow="investigate", stage="pre_llm", target=None)
        assert GATE.applies(ctx) is False


# ---------------------------------------------------------------------------
# evaluate() — agreement / disagreement verdicts
# ---------------------------------------------------------------------------

class TestEvaluate:
    def test_agreement_returns_pass(self) -> None:
        with patch(_DERIVE_PATH, return_value=_rb_verdict("medium")):
            verdict = GATE.evaluate(_ctx(llm_band="medium"))

        assert verdict.verdict == "pass"
        assert verdict.gate_name == "AlgorithmicRiskBandGate"
        assert verdict.severity == "advisory"
        assert "alg=medium == llm=medium" in verdict.reason

    def test_disagreement_returns_warn(self) -> None:
        with patch(_DERIVE_PATH, return_value=_rb_verdict("high")):
            verdict = GATE.evaluate(_ctx(llm_band="low"))

        assert verdict.verdict == "warn"
        assert "alg=high vs llm=low" in verdict.reason

    def test_disagreement_suggested_patch_contains_risk_level(self) -> None:
        with patch(_DERIVE_PATH, return_value=_rb_verdict("high", confidence=0.95)):
            verdict = GATE.evaluate(_ctx(llm_band="none"))

        assert verdict.suggested_patch is not None
        assert verdict.suggested_patch["risk_level"] == "high"
        assert verdict.suggested_patch["failure_state"] == "needs_review"

    def test_confidence_propagated_from_rb_verdict(self) -> None:
        with patch(_DERIVE_PATH, return_value=_rb_verdict("low", confidence=0.8)):
            verdict = GATE.evaluate(_ctx(llm_band="low"))

        assert verdict.confidence == pytest.approx(0.8)

    def test_no_evidence_uses_defaults_without_error(self) -> None:
        ctx = GateContext(flow="investigate", stage="post_llm", target=SimpleNamespace(risk_level="none"))
        with patch(_DERIVE_PATH, return_value=_rb_verdict("none")):
            verdict = GATE.evaluate(ctx)

        assert verdict.verdict == "pass"

    def test_evidence_is_forwarded_to_derive_risk_band(self) -> None:
        evidence = {
            "is_mandatory": True,
            "date_verdict": object(),
            "amount_verdict": object(),
            "relevance_result": object(),
        }
        ctx = _ctx(evidence=evidence, llm_band="high")
        with patch(_DERIVE_PATH, return_value=_rb_verdict("high")) as mock_derive:
            GATE.evaluate(ctx)

        mock_derive.assert_called_once_with(
            is_mandatory=True,
            date_verdict=evidence["date_verdict"],
            amount_verdict=evidence["amount_verdict"],
            relevance=evidence["relevance_result"],
        )

    def test_trace_contains_contributing_factors(self) -> None:
        with patch(_DERIVE_PATH, return_value=_rb_verdict("medium", confidence=0.6)):
            verdict = GATE.evaluate(_ctx(llm_band="high"))

        assert verdict.trace is not None
        assert "contributing_factors" in verdict.trace

    def test_trace_on_disagreement_contains_llm_band(self) -> None:
        with patch(_DERIVE_PATH, return_value=_rb_verdict("high")):
            verdict = GATE.evaluate(_ctx(llm_band="low"))

        assert verdict.trace is not None
        assert verdict.trace.get("llm_band") == "low"


# ---------------------------------------------------------------------------
# Metrics recording
# ---------------------------------------------------------------------------

class TestMetrics:
    def _run_with_mock_metrics(
        self,
        alg_band: str,
        llm_band: str,
        confidence: float = 0.9,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        agree_mock = _make_counter_mock()
        disagree_mock = _make_counter_mock()
        bucket_mock = _make_counter_mock()

        with (
            patch(_DERIVE_PATH, return_value=_rb_verdict(alg_band, confidence)),
            patch(f"{_METRICS_MOD}.algorithmic_risk_band_agreement_total", agree_mock),
            patch(f"{_METRICS_MOD}.algorithmic_risk_band_disagreement_total", disagree_mock),
            patch(
                f"{_METRICS_MOD}.algorithmic_risk_band_confidence_bucket_total",
                bucket_mock,
            ),
        ):
            GATE.evaluate(_ctx(llm_band=llm_band))

        return agree_mock, disagree_mock, bucket_mock

    def test_agreement_increments_agreement_counter(self) -> None:
        agree_mock, disagree_mock, _ = self._run_with_mock_metrics("medium", "medium")
        agree_mock.labels.assert_called_once()
        agree_mock.inc.assert_called_once()
        disagree_mock.labels.assert_not_called()

    def test_disagreement_increments_disagreement_counter(self) -> None:
        agree_mock, disagree_mock, _ = self._run_with_mock_metrics("high", "low")
        disagree_mock.labels.assert_called_once()
        disagree_mock.inc.assert_called_once()
        agree_mock.labels.assert_not_called()

    def test_bucket_counter_called_on_agreement(self) -> None:
        _, _, bucket_mock = self._run_with_mock_metrics("none", "none", confidence=0.9)
        bucket_mock.labels.assert_called_once_with(confidence_bucket="high", agree="true")
        bucket_mock.inc.assert_called_once()

    def test_bucket_counter_called_on_disagreement(self) -> None:
        _, _, bucket_mock = self._run_with_mock_metrics("high", "low", confidence=0.6)
        bucket_mock.labels.assert_called_once_with(confidence_bucket="medium", agree="false")
        bucket_mock.inc.assert_called_once()

    def test_confidence_bucket_low_below_05(self) -> None:
        _, _, bucket_mock = self._run_with_mock_metrics("medium", "medium", confidence=0.3)
        bucket_mock.labels.assert_called_once_with(confidence_bucket="low", agree="true")

    def test_metrics_exception_does_not_raise(self) -> None:
        """Metrics failure must never surface to the caller."""
        broken_counter = MagicMock()
        broken_counter.labels.side_effect = Exception("prometheus down")
        with (
            patch(_DERIVE_PATH, return_value=_rb_verdict("medium")),
            patch(f"{_METRICS_MOD}.algorithmic_risk_band_agreement_total", broken_counter),
        ):
            # Should not raise
            verdict = GATE.evaluate(_ctx(llm_band="medium"))

        assert verdict.verdict == "pass"


# ---------------------------------------------------------------------------
# Gate metadata
# ---------------------------------------------------------------------------

def test_gate_metadata() -> None:
    assert GATE.gate_name == "AlgorithmicRiskBandGate"
    assert GATE.flow == "investigate"
    assert GATE.stage == "post_llm"
    assert GATE.order == 50
    assert GATE.default_severity == "advisory"
