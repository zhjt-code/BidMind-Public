"""Tests for GatePipeline."""

import random

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.pipeline import GatePipeline
from app.core.contracts.failure_state import FailureState
from .conftest import (
    AlwaysPassGate,
    AlwaysRejectGate,
    NotApplicableGate,
    PatchGate,
)


def test_not_applicable_gate_is_skipped(base_context):
    pipeline = GatePipeline([NotApplicableGate(), AlwaysPassGate()])
    verdicts = pipeline.run(base_context)
    assert len(verdicts) == 1
    assert verdicts[0].gate_name == "AlwaysPassGate"


def test_off_severity_gate_is_skipped(base_context):
    from app.core.ai.gates.registry import GateRegistry

    reg = GateRegistry()
    gate = AlwaysPassGate()
    reg.register(gate)
    reg.set_ab_override("test_branch", "AlwaysPassGate", "off")

    from app.core.ai.gates.registry import ab_branch
    token = ab_branch.set("test_branch")
    try:
        pipeline = GatePipeline([gate], registry=reg)
        verdicts = pipeline.run(base_context)
        assert len(verdicts) == 0
    finally:
        ab_branch.reset(token)


def test_patch_aggregation_score_takes_min(base_context):
    target = {"score": 100}
    g1 = PatchGate({"score": 60}, order=10)
    g2 = PatchGate({"score": 40}, order=20)
    pipeline = GatePipeline([g1, g2])
    pipeline.run(base_context, apply_patch_to=target)
    assert target["score"] == 40


def test_patch_aggregation_risk_level_takes_max(base_context):
    target = {"risk_level": "low"}
    g1 = PatchGate({"risk_level": "low"}, order=10)
    g2 = PatchGate({"risk_level": "high"}, order=20)
    pipeline = GatePipeline([g1, g2])
    pipeline.run(base_context, apply_patch_to=target)
    assert target["risk_level"] == "high"


def test_patch_aggregation_compliance_level(base_context):
    target = {"compliance_level": "fully"}
    g1 = PatchGate({"compliance_level": "partially"}, order=10)
    g2 = PatchGate({"compliance_level": "not"}, order=20)
    pipeline = GatePipeline([g1, g2])
    pipeline.run(base_context, apply_patch_to=target)
    assert target["compliance_level"] == "not"


def test_patch_aggregation_failure_state(base_context):
    target = {"failure_state": "completed"}
    g1 = PatchGate({"failure_state": "degraded"}, order=10)
    g2 = PatchGate({"failure_state": "needs_review"}, order=20)
    pipeline = GatePipeline([g1, g2])
    pipeline.run(base_context, apply_patch_to=target)
    assert target["failure_state"] == "needs_review"


def test_run_all_stages_returns_failure_state():
    gates = [AlwaysPassGate()]
    pipeline = GatePipeline(gates)

    ctx_post_llm = GateContext(flow="investigate", stage="post_llm", target={})
    verdicts, state, reason = pipeline.run_all_stages(ctx_post_llm=ctx_post_llm)
    assert state == FailureState.COMPLETED
    assert len(verdicts) == 1


def test_mutation_order_equivalence(base_context):
    """Same-stage gates produce equivalent output regardless of insertion order (pure functions)."""
    g1 = PatchGate({"score": 60}, order=10)
    g2 = PatchGate({"score": 40}, order=20)

    ordered_verdicts = GatePipeline([g1, g2]).run(base_context)
    shuffled_verdicts = GatePipeline([g2, g1]).run(base_context)

    assert len(ordered_verdicts) == len(shuffled_verdicts)
    ordered_names = {v.gate_name for v in ordered_verdicts}
    shuffled_names = {v.gate_name for v in shuffled_verdicts}
    assert ordered_names == shuffled_names
