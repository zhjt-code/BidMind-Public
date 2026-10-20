# -*- coding: utf-8 -*-
"""scheme_reader seed 注册 / flow 节点 / param 对齐契约。

职责: 验证 bid_review.scheme_reader 在 seed_llm_registry 与 FLOW_NODE_KEYS 的
 注册正确性, 以及 ParamDef 与 Prompt 模板占位符对齐(同源防线)。
不做: 不测试 Prompt 文本语义、reader 行为、Dispatcher 或 DB。
"""
from __future__ import annotations

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

_NODE_KEY = "bid_review.scheme_reader"


def _get_node() -> object:
    node = next(
        (n for n in seed_llm_registry.ALL_NODES if n.node_key == _NODE_KEY), None
    )
    assert node is not None, f"{_NODE_KEY} 未在 seed ALL_NODES 注册"
    return node


def test_node_registered() -> None:
    _get_node()


def test_node_call_method_and_params_match_reader() -> None:
    """seed 声明必须与 reader.py 实际调用方式一致(chat_json/0.0/2500)。"""
    node = _get_node()
    assert node.call_method == "chat_json"
    assert node.default_temperature == 0.0
    assert node.default_max_tokens == 2500


def test_node_prompt_refs_resolvable() -> None:
    node = _get_node()
    sys_t = seed_llm_registry._resolve_prompt(node.system_prompt)
    user_t = seed_llm_registry._resolve_prompt(node.user_prompt)
    assert sys_t, "system prompt 解析为空"
    assert user_t, "user prompt 解析为空"


def test_input_params_match_user_template_placeholders() -> None:
    """ 防线: input ParamDef 必须与模板 {占位符} 完全对齐。"""
    from app.core.ai.prompts.scheme_reader import SCHEME_READER_USER
    import re

    placeholders = set(
        re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", SCHEME_READER_USER)
    )
    keys = {
        p.param_key
        for p in seed_llm_registry.PARAM_DEFS
        if p.node_key == _NODE_KEY and p.direction == "input"
    }
    assert keys == placeholders, (
        f"ParamDef 与模板占位符不对齐: params={keys} placeholders={placeholders}"
    )


def test_output_param_declared() -> None:
    out = [
        p for p in seed_llm_registry.PARAM_DEFS
        if p.node_key == _NODE_KEY and p.direction == "output"
    ]
    assert len(out) == 1
    assert out[0].param_key == "scheme_map"


def test_flow_node_keys_includes_scheme_reader() -> None:
    from app.core.ai.prompt_registry import FLOW_NODE_KEYS
    assert _NODE_KEY in FLOW_NODE_KEYS["bid_review"]


def test_edges_registered() -> None:
    edges = [
        (e.from_node_key, e.to_node_key) for e in seed_llm_registry.EDGES
    ]
    assert ("bid_review.scoring_extract", _NODE_KEY) in edges
    assert (_NODE_KEY, "bid_review.scoring_repair") in edges
