"""Critic flow contract tests.

These tests prevent the class of drift where a valid metric flow label
is not wired to the Judge prompt namespace and silently falls back to generic.
"""

from __future__ import annotations

from app.core.ai.critic.flows import (
    CRITIC_BUSINESS_FLOWS,
    FLOW_COLLUSION,
    FLOW_GENERIC,
    FLOW_INVESTIGATE,
    FLOW_REVIEW,
    FLOW_WRITER,
    JUDGE_TEMPLATE_FLOW_BY_METRIC_FLOW,
    resolve_judge_template_flow,
)
from app.core.ai.critic.prompts.judge_templates import (
    FLOW_TEMPLATE_MAP,
    JUDGE_GENERIC_SYSTEM,
    get_judge_templates,
)
from app.core.ai.prompt_registry import FLOW_NODE_KEYS
from scripts.seed_llm_registry import ALL_NODES, PARAM_DEFS

JUDGE_NODE_KEYS = {
    "judge.review",
    "judge.generic",
    "judge.collusion",
    "judge.writer",
}


def test_business_flows_are_registered_once() -> None:
    assert CRITIC_BUSINESS_FLOWS == (
        FLOW_INVESTIGATE,
        FLOW_COLLUSION,
        FLOW_WRITER,
    )
    assert len(CRITIC_BUSINESS_FLOWS) == len(set(CRITIC_BUSINESS_FLOWS))


def test_judge_template_alias_covers_all_flows() -> None:
    expected = {
        FLOW_INVESTIGATE,
        FLOW_REVIEW,
        FLOW_COLLUSION,
        FLOW_WRITER,
        FLOW_GENERIC,
    }
    assert set(JUDGE_TEMPLATE_FLOW_BY_METRIC_FLOW) == expected


def test_investigate_metric_flow_routes_to_review_template_flow() -> None:
    assert resolve_judge_template_flow(FLOW_INVESTIGATE) == FLOW_REVIEW
    suffix, system, _user = get_judge_templates(FLOW_INVESTIGATE)
    assert suffix == "review"
    assert system != JUDGE_GENERIC_SYSTEM


def test_business_flows_do_not_fall_back_to_generic() -> None:
    for flow in CRITIC_BUSINESS_FLOWS:
        suffix, system, _user = get_judge_templates(flow)
        assert suffix != "generic", f"{flow} unexpectedly routed to generic"
        assert system != JUDGE_GENERIC_SYSTEM


def test_unknown_flow_still_fails_safe_to_generic() -> None:
    assert resolve_judge_template_flow("unknown_flow") == FLOW_GENERIC
    suffix, system, _user = get_judge_templates("unknown_flow")
    assert suffix == "generic"
    assert system == JUDGE_GENERIC_SYSTEM


def test_template_map_uses_template_flows_only() -> None:
    assert set(FLOW_TEMPLATE_MAP) == {
        FLOW_REVIEW,
        FLOW_COLLUSION,
        FLOW_WRITER,
        FLOW_GENERIC,
    }


def test_judge_nodes_are_registered_in_seed() -> None:
    nodes = {node.node_key: node for node in ALL_NODES}

    assert JUDGE_NODE_KEYS <= set(nodes), (
        "Judge registry_first 调用点必须同步进入 seed 注册源"
    )
    for node_key in JUDGE_NODE_KEYS:
        node = nodes[node_key]
        assert node.system_prompt is not None, f"{node_key} missing system prompt"
        assert node.user_prompt is not None, f"{node_key} missing user prompt"
        assert node.output_schema_name == "JudgeVerdict"


def test_judge_nodes_have_seed_param_contract() -> None:
    params_by_node = {
        node_key: {
            (param.direction, param.param_key)
            for param in PARAM_DEFS
            if param.node_key == node_key
        }
        for node_key in JUDGE_NODE_KEYS
    }
    expected = {
        ("input", "original_output"),
        ("input", "context_summary"),
        ("input", "evidence_text"),
        ("output", "verdict"),
        ("output", "confidence"),
        ("output", "reason"),
        ("runtime", "temperature"),
        ("runtime", "max_tokens"),
    }

    for node_key, params in params_by_node.items():
        assert expected <= params, f"{node_key} missing params: {expected - params}"


def test_business_judge_nodes_are_in_task_snapshots() -> None:
    assert "judge.review" in FLOW_NODE_KEYS["bid_review"]
    assert "judge.collusion" in FLOW_NODE_KEYS["collusion"]
    assert "judge.writer" in FLOW_NODE_KEYS["writing"]
    assert all("judge.generic" not in keys for keys in FLOW_NODE_KEYS.values())

