"""Tests for FailureStateDeterminer -- all 6 decision tree branches."""

from app.core.ai.gates.base import GateVerdict
from app.core.contracts.failure_state import FailureState
from app.core.contracts.failure_state_determiner import FailureStateDeterminer

det = FailureStateDeterminer()


def _verdict(name: str = "TestGate", verdict: str = "pass", patch: dict | None = None) -> GateVerdict:
    return GateVerdict(
        gate_name=name,
        verdict=verdict,
        severity="enforce",
        reason="test",
        suggested_patch=patch,
    )


def test_step1_task_exception():
    state, reason = det.determine([], task_exception=RuntimeError("boom"))
    assert state == FailureState.FAILED
    assert "RuntimeError" in reason


def test_step2_no_llm_output():
    state, reason = det.determine([], no_llm_output=True)
    assert state == FailureState.INCONCLUSIVE
    assert "No LLM output" in reason


def test_step3_gate_reject():
    v = _verdict(verdict="reject")
    state, reason = det.determine([v])
    assert state == FailureState.NEEDS_REVIEW
    assert "reject" in reason.lower()


def test_step4_gate_rejected_patch():
    v = _verdict(verdict="enforce_patch", patch={"gate_rejected": True})
    state, reason = det.determine([v])
    assert state == FailureState.NEEDS_REVIEW
    assert "conflict" in reason.lower() or "gate_rejected" in reason.lower()


def test_step5_degraded_signals():
    state, reason = det.determine([], degraded_signals={"algorithm_only", "json_truncation_repair"})
    assert state == FailureState.DEGRADED
    assert "algorithm_only" in reason


def test_step6_all_pass():
    v = _verdict(verdict="pass")
    state, reason = det.determine([v])
    assert state == FailureState.COMPLETED
    assert "passed" in reason.lower()


def test_step6_empty_verdicts():
    state, reason = det.determine([])
    assert state == FailureState.COMPLETED


def test_priority_exception_over_no_output():
    """Step 1 takes priority over step 2."""
    state, _ = det.determine([], task_exception=ValueError("x"), no_llm_output=True)
    assert state == FailureState.FAILED


def test_priority_reject_over_degraded():
    """Step 3 takes priority over step 5."""
    v = _verdict(verdict="reject")
    state, _ = det.determine([v], degraded_signals={"fallback"})
    assert state == FailureState.NEEDS_REVIEW
