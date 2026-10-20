"""Shared fixtures for Gate unit tests."""

import pytest

from app.core.ai.gates.base import Gate, GateContext, GateVerdict


class AlwaysPassGate(Gate):
    gate_name = "AlwaysPassGate"
    flow = "all"
    stage = "post_llm"
    order = 10
    default_severity = "advisory"

    def applies(self, ctx: GateContext) -> bool:
        return True

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        return GateVerdict(
            gate_name=self.gate_name,
            verdict="pass",
            severity=self.default_severity,
            reason="always passes",
        )


class AlwaysRejectGate(Gate):
    gate_name = "AlwaysRejectGate"
    flow = "all"
    stage = "post_llm"
    order = 20
    default_severity = "enforce"

    def applies(self, ctx: GateContext) -> bool:
        return True

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        return GateVerdict(
            gate_name=self.gate_name,
            verdict="reject",
            severity=self.default_severity,
            reason="always rejects",
        )


class NotApplicableGate(Gate):
    gate_name = "NotApplicableGate"
    flow = "collusion"
    stage = "post_llm"
    order = 5
    default_severity = "enforce"

    def applies(self, ctx: GateContext) -> bool:
        return False

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        raise AssertionError("Should never be called")


class PatchGate(Gate):
    gate_name = "PatchGate"
    flow = "all"
    stage = "post_llm"
    order = 30
    default_severity = "enforce"

    def __init__(self, patch: dict, order: int = 30):
        self._patch = patch
        self.order = order

    def applies(self, ctx: GateContext) -> bool:
        return True

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason="test patch",
            suggested_patch=self._patch,
        )


@pytest.fixture
def base_context():
    return GateContext(flow="investigate", stage="post_llm", target={})
