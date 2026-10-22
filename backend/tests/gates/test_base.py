"""Tests for Gate base classes."""

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

from .conftest import AlwaysPassGate


def test_gate_verdict_is_frozen():
    v = GateVerdict(gate_name="test", verdict="pass", severity="advisory", reason="ok")
    assert v.gate_name == "test"
    try:
        v.gate_name = "changed"
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_gate_context_is_frozen():
    ctx = GateContext(flow="investigate", stage="post_llm", target={})
    try:
        ctx.flow = "collusion"
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_always_pass_gate():
    gate = AlwaysPassGate()
    ctx = GateContext(flow="investigate", stage="post_llm", target={})
    assert gate.applies(ctx) is True
    v = gate.evaluate(ctx)
    assert v.verdict == "pass"
    assert v.gate_name == "AlwaysPassGate"


def test_gate_is_abstract():
    try:
        Gate()
        assert False, "Gate is abstract"
    except TypeError:
        pass
