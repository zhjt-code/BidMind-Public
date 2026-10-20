"""Tests for GateRegistry."""

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.registry import GateRegistry, ab_branch
from .conftest import AlwaysPassGate


def test_register_and_list():
    reg = GateRegistry()
    gate = AlwaysPassGate()
    reg.register(gate)
    assert gate in reg.list_gates()
    assert gate in reg.list_by_flow("investigate")


def test_default_severity():
    reg = GateRegistry()
    gate = AlwaysPassGate()
    reg.register(gate)
    ctx = GateContext(flow="investigate", stage="post_llm", target={})
    assert reg.resolve_severity(gate, ctx) == "advisory"


def test_global_override():
    reg = GateRegistry(global_overrides='{"AlwaysPassGate": "enforce"}')
    gate = AlwaysPassGate()
    reg.register(gate)
    ctx = GateContext(flow="investigate", stage="post_llm", target={})
    assert reg.resolve_severity(gate, ctx) == "enforce"


def test_project_override_beats_global():
    reg = GateRegistry(global_overrides='{"AlwaysPassGate": "enforce"}')
    gate = AlwaysPassGate()
    reg.register(gate)
    reg.set_project_override("proj_1", "AlwaysPassGate", "reject")
    ctx = GateContext(flow="investigate", stage="post_llm", target={}, config={"project_id": "proj_1"})
    assert reg.resolve_severity(gate, ctx) == "reject"


def test_ab_override_beats_project():
    reg = GateRegistry()
    gate = AlwaysPassGate()
    reg.register(gate)
    reg.set_project_override("proj_1", "AlwaysPassGate", "reject")
    reg.set_ab_override("candidate", "AlwaysPassGate", "off")

    ctx = GateContext(flow="investigate", stage="post_llm", target={}, config={"project_id": "proj_1"})
    token = ab_branch.set("candidate")
    try:
        assert reg.resolve_severity(gate, ctx) == "off"
    finally:
        ab_branch.reset(token)


def test_invalid_global_overrides_graceful():
    reg = GateRegistry(global_overrides="not valid json")
    gate = AlwaysPassGate()
    reg.register(gate)
    ctx = GateContext(flow="investigate", stage="post_llm", target={})
    assert reg.resolve_severity(gate, ctx) == "advisory"
