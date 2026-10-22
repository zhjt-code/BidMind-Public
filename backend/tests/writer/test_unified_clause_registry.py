"""writing.unified_clause 的 seed / FLOW / 占位符契约。

职责: 锁定写标提取运行时绑定的 node_key 必须已 seed，且 unified 节点
      的 PromptRef、ParamDef、node_class 与调用约定一致。
不做: 不测提取语义、不去重、不打真实模型、不连数据库。
"""

from __future__ import annotations

import importlib
import re

from app.core.writer.clause_extractor import _LAYER_NODE_KEY_MAP
from app.core.writer.prompts.clause_extract_unified import (
    UNIFIED_CLAUSE_EXTRACT_USER,
    UNIFIED_CLAUSE_NODE_KEY,
)


def _seed():
    return importlib.import_module("scripts.seed_llm_registry")


def _bound_extract_node_keys() -> set[str]:
    """提取运行时实际绑定的 node_key（单次过正文 + 仍保留的分层回退）。"""
    return {UNIFIED_CLAUSE_NODE_KEY, *_LAYER_NODE_KEY_MAP.values()}


def test_writer_extract_bound_node_keys_are_seeded() -> None:
    """形态护栏：运行时绑定的提取 node_key 必须在 ALL_NODES。

 夹具1: writing.unified_clause（本案例，）
 夹具2: writing.hard_clause / structure_clause / guide_clause（同形态另一实体）
 """
    seed = _seed()
    seeded = {node.node_key for node in seed.ALL_NODES}
    missing = _bound_extract_node_keys() - seeded
    assert not missing, f"写标提取运行时绑定了未 seed 的节点: {missing}"


def test_unified_clause_node_seeded_with_runtime_contract() -> None:
    seed = _seed()
    node = next(
        (item for item in seed.ALL_NODES if item.node_key == UNIFIED_CLAUSE_NODE_KEY),
        None,
    )
    assert node is not None, f"{UNIFIED_CLAUSE_NODE_KEY} 未在 seed ALL_NODES 注册"
    assert node.stage_key == "clause_extract"
    assert node.flow_key == "writing"
    assert node.call_method == "chat_json"
    assert node.default_temperature == 0.1
    assert node.default_max_tokens == 6000
    assert node.node_class == "mechanical"
    assert node.system_prompt is not None
    assert node.user_prompt is not None
    sys_text = seed._resolve_prompt(node.system_prompt)
    user_text = seed._resolve_prompt(node.user_prompt)
    assert sys_text, "unified_clause system prompt 解析为空"
    assert user_text, "unified_clause user prompt 解析为空"


def test_unified_clause_input_params_match_user_placeholders() -> None:
    """ 同形态：input ParamDef 必须与 user 模板占位符对齐。"""
    seed = _seed()
    placeholders = set(
        re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", UNIFIED_CLAUSE_EXTRACT_USER)
    )
    keys = {
        item.param_key
        for item in seed.PARAM_DEFS
        if item.node_key == UNIFIED_CLAUSE_NODE_KEY and item.direction == "input"
    }
    assert keys == placeholders, (
        f"ParamDef 与模板占位符不对齐: params={keys} placeholders={placeholders}"
    )


def test_unified_clause_output_params_declare_three_layers() -> None:
    seed = _seed()
    outputs = {
        item.param_key
        for item in seed.PARAM_DEFS
        if item.node_key == UNIFIED_CLAUSE_NODE_KEY and item.direction == "output"
    }
    assert outputs == {"hard", "structure", "guide"}


def test_unified_clause_is_in_writing_flow_snapshot() -> None:
    from app.core.ai.prompt_registry import FLOW_NODE_KEYS

    assert UNIFIED_CLAUSE_NODE_KEY in FLOW_NODE_KEYS["writing"]
