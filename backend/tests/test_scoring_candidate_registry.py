# -*- coding: utf-8 -*-
"""scoring_table_structure / scoring_table_candidate seed 注册对齐契约。

职责: 验证两个Task3新增节点在 seed_llm_registry 与 FLOW_NODE_KEYS 的注册
 正确性, 以及 ParamDef 与 Prompt 模板占位符对齐(同源防线)。
不做: 不测试 Prompt 文本语义、reader 行为、Dispatcher 或 DB。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_backend_root = Path(__file__).resolve().parent.parent
_scripts_path = _backend_root / "scripts"
for _p in (_backend_root, _scripts_path):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")

pytestmark = [pytest.mark.scoring, pytest.mark.usefixtures('restore_real_ai_modules')]

import seed_llm_registry  # type: ignore[import-not-found]

_STRUCTURE_NODE_KEY = "bid_review.scoring_table_structure"
_CANDIDATE_NODE_KEY = "bid_review.scoring_table_candidate"


def _get_node(node_key: str) -> object:
    node = next(
        (n for n in seed_llm_registry.ALL_NODES if n.node_key == node_key), None
    )
    assert node is not None, f"{node_key} 未在 seed ALL_NODES 注册"
    return node


@pytest.mark.parametrize("node_key", [_STRUCTURE_NODE_KEY, _CANDIDATE_NODE_KEY])
def test_node_registered(node_key: str) -> None:
    _get_node(node_key)


def test_structure_node_call_method_and_params() -> None:
    node = _get_node(_STRUCTURE_NODE_KEY)
    assert node.call_method == "chat_json"
    assert node.default_temperature == 0.0
    assert node.default_max_tokens == 2500
    assert node.execution_channel == "review"
    assert node.visibility == "technical"
    assert node.editable_level == "advanced"
    assert node.stage_key == "tender_extract"


def test_candidate_node_call_method_and_params() -> None:
    node = _get_node(_CANDIDATE_NODE_KEY)
    assert node.call_method == "chat_json"
    assert node.default_temperature == 0.0
    # Task7真实gate发现: 4000旧值对真实33行逻辑表候选响应会截断,
    # 按真实证据上调至8000, 见candidate_reader._MAX_TOKENS同名断言。
    assert node.default_max_tokens == 8000
    assert node.execution_channel == "review"
    assert node.visibility == "technical"
    assert node.editable_level == "advanced"
    assert node.stage_key == "tender_extract"


@pytest.mark.parametrize("node_key", [_STRUCTURE_NODE_KEY, _CANDIDATE_NODE_KEY])
def test_node_prompt_refs_resolvable(node_key: str) -> None:
    node = _get_node(node_key)
    sys_t = seed_llm_registry._resolve_prompt(node.system_prompt)
    user_t = seed_llm_registry._resolve_prompt(node.user_prompt)
    assert sys_t, "system prompt 解析为空"
    assert user_t, "user prompt 解析为空"


def test_structure_input_params_match_user_template_placeholders() -> None:
    from app.core.ai.prompts.scoring_table_structure import (
        SCORING_TABLE_STRUCTURE_USER,
    )

    placeholders = set(
        re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", SCORING_TABLE_STRUCTURE_USER)
    )
    keys = {
        p.param_key
        for p in seed_llm_registry.PARAM_DEFS
        if p.node_key == _STRUCTURE_NODE_KEY and p.direction == "input"
    }
    assert keys == placeholders, (
        f"ParamDef 与模板占位符不对齐: params={keys} placeholders={placeholders}"
    )


def test_candidate_input_params_match_user_template_placeholders() -> None:
    from app.core.ai.prompts.scoring_table_candidate import (
        SCORING_TABLE_CANDIDATE_USER,
    )

    placeholders = set(
        re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", SCORING_TABLE_CANDIDATE_USER)
    )
    keys = {
        p.param_key
        for p in seed_llm_registry.PARAM_DEFS
        if p.node_key == _CANDIDATE_NODE_KEY and p.direction == "input"
    }
    assert keys == placeholders, (
        f"ParamDef 与模板占位符不对齐: params={keys} placeholders={placeholders}"
    )


def test_structure_output_param_declared() -> None:
    out = [
        p for p in seed_llm_registry.PARAM_DEFS
        if p.node_key == _STRUCTURE_NODE_KEY and p.direction == "output"
    ]
    assert len(out) == 1
    assert out[0].param_key == "document_structure"


def test_candidate_output_param_declared() -> None:
    out = [
        p for p in seed_llm_registry.PARAM_DEFS
        if p.node_key == _CANDIDATE_NODE_KEY and p.direction == "output"
    ]
    assert len(out) == 1
    assert out[0].param_key == "table_candidate"


def test_flow_node_keys_includes_both_nodes() -> None:
    from app.core.ai.prompt_registry import FLOW_NODE_KEYS

    assert _STRUCTURE_NODE_KEY in FLOW_NODE_KEYS["bid_review"]
    assert _CANDIDATE_NODE_KEY in FLOW_NODE_KEYS["bid_review"]


def test_both_nodes_use_deterministic_stability_class() -> None:
    """bid_review.* 前缀节点必须强制temperature=0.0(S.3.2稳定性等级)。"""
    from app.core.ai.prompt_registry import _derive_stability_class

    assert _derive_stability_class(_STRUCTURE_NODE_KEY) == "deterministic"
    assert _derive_stability_class(_CANDIDATE_NODE_KEY) == "deterministic"


def test_display_order_unique_after_current_tail() -> None:
    """display_order 必须使用当前末尾(scheme_reader=12)之后的唯一值, 且互不相同。"""
    structure = _get_node(_STRUCTURE_NODE_KEY)
    candidate = _get_node(_CANDIDATE_NODE_KEY)
    scheme_reader = _get_node("bid_review.scheme_reader")
    assert structure.display_order > scheme_reader.display_order
    assert candidate.display_order > scheme_reader.display_order
    assert structure.display_order != candidate.display_order
