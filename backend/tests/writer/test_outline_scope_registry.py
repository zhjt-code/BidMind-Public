def test_outline_scope_titles_registered_in_prompt_registry_flow() -> None:
    from app.core.ai.prompt_registry import FLOW_NODE_KEYS

    writing_keys = FLOW_NODE_KEYS["writing"]
    assert "writing.outline_scope_titles" in writing_keys
    enrich_at = writing_keys.index("writing.skeleton_enrich")
    scope_at = writing_keys.index("writing.outline_scope_titles")
    assert scope_at == enrich_at + 1


def test_outline_scope_titles_node_seeded_for_llm_center() -> None:
    from scripts.seed_llm_registry import ALL_NODES, EDGES, PARAM_DEFS

    node = next(
        item for item in ALL_NODES
        if item.node_key == "writing.outline_scope_titles"
    )
    assert node.stage_key == "outline_generate"
    assert node.system_prompt is not None
    assert node.user_prompt is not None
    assert node.call_method == "chat_json"

    param_keys = {
        item.param_key
        for item in PARAM_DEFS
        if item.node_key == "writing.outline_scope_titles"
    }
    assert "scope_text" in param_keys
    assert "chapter_title" in param_keys
    assert "scope_titles" in param_keys

    assert any(
        edge.from_node_key == "writing.skeleton_enrich"
        and edge.to_node_key == "writing.outline_scope_titles"
        for edge in EDGES
    )
