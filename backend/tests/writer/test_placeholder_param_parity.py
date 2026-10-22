"""防御测试：writing 流程节点的模板占位符必须全部登记到 PARAM_DEFS。

背景:
 P2.3 给 ``writing.chapter_profile`` 的 CHAPTER_PROFILE_USER 新增了两个
 上下文占位符 ``{industry_profile_excerpt}`` / ``{clause_domain_summary}``，
 但 ``seed_llm_registry.PARAM_DEFS`` 中该节点历史上一条输入参数都没有，
 导致 LLM Center UI 点击 "校验" 时触发
 ``[user] 新增未定义占位符: {...}`` 报错（见
 ``llm_center_service._validate_draft`` 里的 placeholder_match 校验）。

历史债务:
 引入本测试后发现**绝大多数 writing 节点都有同样的 PARAM_DEFS 遗漏**。
 这些节点历史上从未有人在 LLM Center 点校验按钮，问题一直被掩盖。
 全量补齐需要一次性往 seed 里追加数十条 ParamDef，属于独立的技术债清理
 任务，不应阻塞本次 chapter_profile 回归修复。

测试策略 (最小回归栅栏 + 显式债务清单):
 * 对 ``_GREENLIST_NODES``: 强断言模板占位符是 PARAM_DEFS input 的子集。
 新接入 LLM Center 校验流程的节点必须进入白名单，保证不退化。
 * 对 ``_KNOWN_DEBT_NODES``: 仅生成 ``pytest.xfail`` 并计数，
 作为后续"seed_llm_registry PARAM_DEFS 补齐"工单的可见化依据，
 当某个债务节点被人工补齐后会触发 XPASS，提示移入 greenlist。
"""

from __future__ import annotations

import importlib
import re
from collections import defaultdict

import pytest

from scripts.seed_llm_registry import _WRITING_NODES, PARAM_DEFS

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


_GREENLIST_NODES: frozenset[str] = frozenset(
    {
        "writing.chapter_profile",
        "writing.hard_clause",
        "writing.fixed_template_recognize",
        "writing.relevance_classify",
        "writing.material_pair_judge",
        "writing.section_plan",
        "writing.length_adjust",
        "writing.data_fill",  # industry_profile_excerpt 已同步 ParamDef
        "writing.unified_clause",
        "writing.outline_scope_titles",
    }
)
"""必须保持占位符与 PARAM_DEFS 一致的节点。新节点或修复完成的节点加入此集合。"""


_KNOWN_DEBT_NODES: frozenset[str] = frozenset(
    {
        "writing.structure_clause",
        "writing.guide_clause",
        "writing.render_config",
        "writing.clause_dedup_decision",
        "writing.bid_structure_analysis",
        "writing.bid_directory_extract",
        "writing.clause_classify",
        "writing.industry_profile",
        "writing.skeleton_generate",
        "writing.section_fill",
        "writing.skeleton_enrich",
        "writing.material_tag",
        "writing.image_classify",
        "writing.chapter_judge",
        "writing.free_writing",
        "writing.section_summary",
        "writing.cross_ref_check",
    }
)
"""历史上 PARAM_DEFS 不完整的节点清单，后续应逐个补齐并移入 greenlist。"""


def _load_prompt_text(ref) -> str:
    if ref is None:
        return ""
    try:
        module = importlib.import_module(ref.module)
    except ModuleNotFoundError:
        return ""
    return getattr(module, ref.constant, "") or ""


def _extract_placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text or ""))


def _collect_input_params() -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for pd in PARAM_DEFS:
        if pd.direction == "input":
            result[pd.node_key].add(pd.param_key)
    return result


_NODE_BY_KEY = {nd.node_key: nd for nd in _WRITING_NODES}
_INPUT_PARAMS_BY_NODE = _collect_input_params()


@pytest.mark.parametrize("node_key", sorted(_GREENLIST_NODES))
def test_greenlist_node_placeholders_declared(node_key):
    """Greenlist 节点：模板占位符必须全部在 PARAM_DEFS (input) 中登记。"""
    node_def = _NODE_BY_KEY.get(node_key)
    assert node_def is not None, f"Greenlist 指向未知 node_key: {node_key}"

    system_text = _load_prompt_text(node_def.system_prompt)
    user_text = _load_prompt_text(node_def.user_prompt)
    placeholders = _extract_placeholders(system_text) | _extract_placeholders(user_text)

    declared = _INPUT_PARAMS_BY_NODE.get(node_key, set())
    missing = placeholders - declared
    assert not missing, (
        f"节点 {node_key} 模板中存在未登记占位符: {sorted(missing)}。"
        f"请在 backend/scripts/seed_llm_registry.py 的 PARAM_DEFS 中补充对应 "
        f"ParamDef(direction='input', ...) 并重跑 seed 脚本。"
    )


@pytest.mark.parametrize("node_key", sorted(_KNOWN_DEBT_NODES))
def test_known_debt_nodes_still_missing(node_key):
    """已知历史债务节点：现阶段允许不完整，但一旦被补齐应触发 XPASS 提示。"""
    node_def = _NODE_BY_KEY.get(node_key)
    if node_def is None:
        pytest.skip(f"节点 {node_key} 已从 _WRITING_NODES 移除，请同步清理债务清单")

    system_text = _load_prompt_text(node_def.system_prompt)
    user_text = _load_prompt_text(node_def.user_prompt)
    placeholders = _extract_placeholders(system_text) | _extract_placeholders(user_text)
    declared = _INPUT_PARAMS_BY_NODE.get(node_key, set())

    if placeholders - declared:
        pytest.xfail(
            f"节点 {node_key} PARAM_DEFS 缺失占位符 "
            f"{sorted(placeholders - declared)} (历史债务, 见 test 文档)"
        )


def test_greenlist_and_debt_sets_disjoint():
    overlap = _GREENLIST_NODES & _KNOWN_DEBT_NODES
    assert not overlap, f"greenlist 与历史债务清单不应重叠: {sorted(overlap)}"


def test_debt_and_greenlist_cover_all_writing_nodes_with_placeholders():
    """防御：新增 writing 节点时必须显式归类到 greenlist 或 debt 清单。"""
    uncategorized: list[str] = []
    known = _GREENLIST_NODES | _KNOWN_DEBT_NODES
    for nd in _WRITING_NODES:
        if nd.node_key in known:
            continue
        system_text = _load_prompt_text(nd.system_prompt)
        user_text = _load_prompt_text(nd.user_prompt)
        if _extract_placeholders(system_text) or _extract_placeholders(user_text):
            uncategorized.append(nd.node_key)

    assert not uncategorized, (
        f"检测到未分类 writing 节点: {uncategorized}。请评估其 PARAM_DEFS "
        f"完整性后加入 _GREENLIST_NODES 或 _KNOWN_DEBT_NODES。"
    )
