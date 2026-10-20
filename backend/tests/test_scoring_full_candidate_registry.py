"""scoring_full_candidate seed 注册 / flow 节点 / param 对齐 / edge 结构契约。

职责: 验证 seed_llm_registry NodeDef、ParamDef、EdgeDef 与 FLOW_NODE_KEYS 的注册正确性。
不做: 不测试 Prompt 文本、extractor 行为、Dispatcher 或 DB。
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

# 恢复真实 app.core.ai 模块（见 conftest.py restore_real_ai_modules）
pytestmark = [pytest.mark.scoring, pytest.mark.usefixtures('restore_real_ai_modules')]

import seed_llm_registry  # type: ignore[import-not-found]

_NODE_KEY = "bid_review.scoring_full_candidate"


# ── NodeDef 注册 ──────────────────────────────────────────────────

def _get_node() -> object:
    node = next((n for n in seed_llm_registry.ALL_NODES if n.node_key == _NODE_KEY), None)
    assert node is not None, f"{_NODE_KEY} 未在 seed ALL_NODES 注册"
    return node


def test_node_registered() -> None:
    _get_node()


def test_node_call_method() -> None:
    assert _get_node().call_method == "chat_with_validation"


def test_node_max_tokens() -> None:
    assert _get_node().default_max_tokens == 5000


def test_node_output_schema() -> None:
    assert _get_node().output_schema_name == "FullCandidateScope"


def test_node_prompt_refs_resolvable() -> None:
    node = _get_node()
    sys_t = seed_llm_registry._resolve_prompt(node.system_prompt)
    user_t = seed_llm_registry._resolve_prompt(node.user_prompt)
    assert sys_t, "system prompt 解析为空"
    assert user_t, "user prompt 解析为空"


# ── ParamDef 对齐 ─────────────────────────────────────────────────

def _params(direction: str) -> list:
    return [p for p in seed_llm_registry.PARAM_DEFS
            if p.node_key == _NODE_KEY and p.direction == direction]


def test_param_count_7_input_1_output() -> None:
    inp = _params("input")
    out = _params("output")
    assert len(inp) == 7, f"应有 7 个 input，实际: {[p.param_key for p in inp]}"
    assert len(out) == 1, f"应有 1 个 output，实际: {[p.param_key for p in out]}"


def test_input_param_keys_match_placeholders() -> None:
    keys = {p.param_key for p in _params("input")}
    expected = {
        "target_scheme", "target_lot", "scoring_basis",
        "existing_items", "volume_defs", "diagnostics", "evidence_units",
    }
    assert not (expected - keys), f"缺少 input 参数: {expected - keys}"


# ── EdgeDef 结构 ──────────────────────────────────────────────────

def _edges() -> list[tuple[str, str]]:
    return [(e.from_node_key, e.to_node_key) for e in seed_llm_registry.EDGES]


def test_repair_to_full_candidate_edge() -> None:
    """scoring_repair → scoring_full_candidate 是唯一直接前驱。"""
    assert ("bid_review.scoring_repair", _NODE_KEY) in _edges()


def test_no_extract_bypass_to_full_candidate() -> None:
    """scoring_extract → scoring_full_candidate 旁路不得存在。"""
    assert ("bid_review.scoring_extract", _NODE_KEY) not in _edges(), (
        "extract 应经 repair 再到 full_candidate，不得直接旁路"
    )


def test_extract_to_repair_edge_conditional() -> None:
    for e in seed_llm_registry.EDGES:
        if e.from_node_key == "bid_review.scoring_extract" and e.to_node_key == "bid_review.scoring_repair":
            assert e.edge_type == "conditional"
            return
    raise AssertionError("scoring_extract → scoring_repair 边未注册")


def test_repair_to_full_candidate_edge_conditional() -> None:
    for e in seed_llm_registry.EDGES:
        if e.from_node_key == "bid_review.scoring_repair" and e.to_node_key == _NODE_KEY:
            assert e.edge_type == "conditional"
            return
    raise AssertionError("scoring_repair → full_candidate 边未注册")


# ── FLOW_NODE_KEYS 护栏 ───────────────────────────────────────────

def test_flow_node_keys_includes_full_candidate() -> None:
    from app.core.ai.prompt_registry import FLOW_NODE_KEYS
    assert _NODE_KEY in FLOW_NODE_KEYS["bid_review"], (
        f"{_NODE_KEY} 未在 FLOW_NODE_KEYS['bid_review']"
    )


def test_flow_node_keys_all_seeded() -> None:
    from app.core.ai.prompt_registry import FLOW_NODE_KEYS
    seeded = {n.node_key for n in seed_llm_registry.ALL_NODES}
    missing = {nk for keys in FLOW_NODE_KEYS.values() for nk in keys} - seeded
    assert not missing, f"FLOW_NODE_KEYS 引用了未 seed 的节点: {missing}"


def test_bid_review_flow_keys_cover_known_nodes() -> None:
    from app.core.ai.prompt_registry import FLOW_NODE_KEYS
    required = {
        "bid_review.tender_extract_page", "bid_review.scoring_extract",
        "bid_review.scoring_repair", "bid_review.scoring_full_candidate",
        "bid_review.relevance_classify",
    }
    assert required <= set(FLOW_NODE_KEYS["bid_review"])
