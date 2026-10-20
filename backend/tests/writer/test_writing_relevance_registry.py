def test_writing_relevance_node_registered_in_prompt_registry_flow() -> None:
    from app.core.ai.prompt_registry import FLOW_NODE_KEYS

    assert "writing.relevance_classify" in FLOW_NODE_KEYS["writing"]


def test_writing_relevance_node_seeded_for_llm_center() -> None:
    from scripts.seed_llm_registry import ALL_NODES

    node = next(
        item for item in ALL_NODES
        if item.node_key == "writing.relevance_classify"
    )

    assert node.stage_key == "clause_classify"
    assert node.system_prompt is not None
    assert node.user_prompt is not None
