"""投标审查 Prompt Registry 链路契约测试。"""

from __future__ import annotations

import sys
from pathlib import Path

from app.core.ai.prompt_registry import FLOW_NODE_KEYS

# scripts/seed_llm_registry.py 不在常规 import path，按 conftest 风格补 path
_backend_root = Path(__file__).resolve().parent.parent
_scripts_path = _backend_root / "scripts"
if str(_scripts_path) not in sys.path:
    sys.path.insert(0, str(_scripts_path))

import seed_llm_registry  # type: ignore[import-not-found]


def test_bid_review_flow_node_keys_cover_full_prompt_chain() -> None:
    nodes = set(FLOW_NODE_KEYS["bid_review"])

    assert {
        "bid_review.tender_extract_page",
        "bid_review.tender_reflection",
        "bid_review.category_merge_decision",
        "bid_review.secondary_merge_decision",
        "bid_review.bid_extract_match",
        "bid_review.review_item",
        "bid_review.review_no_response",
        "bid_review.image_recognition",
        "bid_review.scoring_extract",
        "bid_review.relevance_classify",
        "judge.review",
    }.issubset(nodes)


def test_flow_node_keys_are_all_registered_in_seed() -> None:
    """护栏：FLOW_NODE_KEYS 引用的每个节点都必须在 seed ALL_NODES 注册。

    防止 registry drift：业务任务快照(install_task_snapshot)按 FLOW_NODE_KEYS
    锁版本，若其中节点未经 seed 注册到 DB，运行时 get_prompt 必然 miss 并触发
    "节点未注册" WARNING + 静默 fallback（即 relevance_classify 遗漏的同源根因）。
    """
    seeded = {nd.node_key for nd in seed_llm_registry.ALL_NODES}
    referenced = {nk for keys in FLOW_NODE_KEYS.values() for nk in keys}
    missing = referenced - seeded
    assert not missing, (
        f"FLOW_NODE_KEYS 引用了未在 seed 注册的节点: {missing}。"
        "请在 scripts/seed_llm_registry.py 注册对应 NodeDef 后重跑 seed。"
    )


def test_relevance_classify_prompt_constants_resolve() -> None:
    """护栏：relevance_classify 的 PromptRef 常量必须可解析且非空。

    若 prompt 模块重命名常量，seed 的 _resolve_prompt 会静默存空版本，运行时
    fallback 到代码默认值但 LLM Center 显示空白，难以察觉。user 模板必须保留
    {requirements_json} 占位符，否则业务侧 .format(requirements_json=...) 会失败。
    """
    node = next(
        nd
        for nd in seed_llm_registry.ALL_NODES
        if nd.node_key == "bid_review.relevance_classify"
    )
    sys_text = seed_llm_registry._resolve_prompt(node.system_prompt)
    user_text = seed_llm_registry._resolve_prompt(node.user_prompt)
    assert sys_text, "relevance_classify 的 system prompt 常量解析为空"
    assert user_text, "relevance_classify 的 user prompt 常量解析为空"
    assert "{requirements_json}" in user_text, (
        "relevance_classify user 模板缺少 {requirements_json} 占位符"
    )
