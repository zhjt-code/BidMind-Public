import importlib


def _seed():
    return importlib.import_module("scripts.seed_llm_registry")


def test_seed_registers_fixed_template_recognize_node() -> None:
    seed = _seed()
    nodes = {node.node_key: node for node in seed.ALL_NODES}

    assert "writing.fixed_template_recognize" in nodes
    node = nodes["writing.fixed_template_recognize"]
    assert node.stage_key == "clause_extract"
    assert node.call_method == "chat_json"
    assert node.default_max_tokens == 6000
    assert node.system_prompt.constant == "FIXED_TEMPLATE_RECOGNIZE_SYSTEM"
    assert node.user_prompt.constant == "FIXED_TEMPLATE_RECOGNIZE_USER"


def test_seed_deprecates_old_validate_node() -> None:
    seed = _seed()
    assert "writing.fixed_template_validate" in seed.DEPRECATED_NODE_KEYS


def test_param_defs_cover_recognize_node() -> None:
    seed = _seed()
    params = [
        (item.param_key, item.direction)
        for item in seed.PARAM_DEFS
        if item.node_key == "writing.fixed_template_recognize"
    ]
    inputs = {key for key, direction in params if direction == "input"}
    outputs = {key for key, direction in params if direction == "output"}
    assert {"chunks_text", "page_range_hint", "volume_type_hint"} <= inputs
    assert "templates" in outputs


def test_flow_snapshot_uses_recognize_node() -> None:
    from app.core.ai import prompt_registry

    keys = prompt_registry.FLOW_NODE_KEYS["writing"]
    assert "writing.fixed_template_recognize" in keys
    assert "writing.fixed_template_validate" not in keys
