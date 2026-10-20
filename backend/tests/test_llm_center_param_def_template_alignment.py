"""LLM Center 节点输入参数白名单与 Prompt 模板占位符对齐契约测试。

职责: 验证 seed_llm_registry.py 中各业务 flow 节点的输入 ParamDef 集合
 ⊇ 对应 Prompt 模板（system + user）的 `{占位符}` 集合，
 防止 LLM Center 的 validate_service.validate_draft 在批量校验时
 因 `template.format(**white_list)` 抛 KeyError。
不做: 不连接数据库（仅校验代码侧 PARAM_DEFS 列表与 prompt 字符串），
 不验证 LLM Center 实际校验链路，不测前端。

覆盖范围（按已暴露 Bug 的 flow 优先纳入）:
 - bid_review.bid_extract_match / review_item / review_no_response (投标审查段)
 - collusion.tender_format_extract / route_extract / pair_compare_entity /
 pair_compare_text / pair_compare_business / single_dim_search /
 pair_judgement / group_analysis / project_summary / overall_synthesis /
 industry_signal / industry_profile_gen (串标审查段)

背景:
 LLM Center 的 validate_service 用 `LlmParamDef.direction=='input'` 表中
 的 param_key 集合作为白名单，对每条 draft template 做 `.format(**白名单)`
 校验。如果模板含未注册占位符，会立即抛 KeyError（参见）。

 本测试是种子数据与 prompt 模板的"单一真相对齐"契约，防止以后修改
 prompt 占位符时种子脚本忘记同步。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# scripts 目录下的 seed_llm_registry.py 不在常规 import path，按 conftest 风格补 path
_backend_root = Path(__file__).resolve().parent.parent
_scripts_path = _backend_root / "scripts"
if str(_scripts_path) not in sys.path:
    sys.path.insert(0, str(_scripts_path))

from app.core.ai.prompts.bid_extract import BID_EXTRACT_SYSTEM, BID_EXTRACT_USER
from app.core.ai.prompts.review import (
    NO_RESPONSE_PROMPT,
    REVIEW_PROMPT,
    REVIEW_SYSTEM_PROMPT,
)
from app.core.ai.prompts.scoring_extract import (
    SCORING_EXTRACT_SYSTEM,
    SCORING_EXTRACT_USER,
)
from app.core.ai.prompts.scoring_repair import (
    SCORING_REPAIR_SYSTEM,
    SCORING_REPAIR_USER,
)
from app.core.ai.prompts.scoring_vlm_rescue import (
    SCORING_VLM_RESCUE_SYSTEM,
    SCORING_VLM_RESCUE_USER,
)
from app.core.ai.prompts.relevance_classify import (
    RELEVANCE_CLASSIFY_SYSTEM,
    RELEVANCE_CLASSIFY_USER,
)
from app.core.collusion.prompts import (
    COLLUSION_BUSINESS_PROMPT,
    COLLUSION_ENTITY_PROMPT,
    COLLUSION_EXTRACT_ROUTE_PROMPT,
    COLLUSION_GROUP_ANALYSIS_PROMPT,
    COLLUSION_INDUSTRY_PROFILE_PROMPT_V2,
    COLLUSION_INDUSTRY_SIGNAL_PROMPT,
    COLLUSION_PAIR_JUDGEMENT_PROMPT,
    COLLUSION_PROJECT_SUMMARY_PROMPT,
    COLLUSION_SINGLE_DIM_PROMPT,
    COLLUSION_SYSTEM_PROMPT,
    COLLUSION_TENDER_FORMAT_PROMPT,
    COLLUSION_TEXT_PATTERN_PROMPT,
)

import seed_llm_registry  # type: ignore[import-not-found]

pytestmark = pytest.mark.scoring


# `{name}` 占位符；`{{...}}` 是 JSON 字面量的双花括号转义，不应被识别
_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")


def _extract_placeholders(template: str) -> set[str]:
    """从模板字符串提取 `{name}` 占位符集合，排除 `{{...}}` JSON 字面量。"""
    return set(_PLACEHOLDER_RE.findall(template))


def _build_input_whitelist(node_key: str) -> set[str]:
    """从 seed_llm_registry.PARAM_DEFS 提取指定节点的 input 白名单。

    与 validate_service.validate_draft 的取数逻辑保持一致：
    direction == 'input' 且 status='active'（seed 默认即 active）。
    """
    return {
        pd.param_key
        for pd in seed_llm_registry.PARAM_DEFS
        if pd.node_key == node_key and pd.direction == "input"
    }


# ---------------------------------------------------------------------------
# Node → (system, user) 模板映射表（声明式，便于扩展）
# ---------------------------------------------------------------------------
#
# 每个 case = (test_id, node_key, system_template, user_template)
# system_template 为空字符串表示该节点不使用 system 模板；user_template 必填。

_ALIGNMENT_CASES: list[tuple[str, str, str, str]] = [
    # bid_review 段（投标审查修复锚点）——
    ("bid_review.bid_extract_match",
     "bid_review.bid_extract_match", BID_EXTRACT_SYSTEM, BID_EXTRACT_USER),
    ("bid_review.review_item",
     "bid_review.review_item", REVIEW_SYSTEM_PROMPT, REVIEW_PROMPT),
    ("bid_review.review_no_response",
     "bid_review.review_no_response", REVIEW_SYSTEM_PROMPT, NO_RESPONSE_PROMPT),
    ("bid_review.scoring_extract",
     "bid_review.scoring_extract", SCORING_EXTRACT_SYSTEM, SCORING_EXTRACT_USER),
    ("bid_review.scoring_repair",
     "bid_review.scoring_repair", SCORING_REPAIR_SYSTEM, SCORING_REPAIR_USER),
    ("bid_review.scoring_vlm_rescue",
     "bid_review.scoring_vlm_rescue", SCORING_VLM_RESCUE_SYSTEM, SCORING_VLM_RESCUE_USER),
    ("bid_review.relevance_classify",
     "bid_review.relevance_classify", RELEVANCE_CLASSIFY_SYSTEM, RELEVANCE_CLASSIFY_USER),

    # collusion 段（串标审查修复锚点）——
    # 串标的 system_prompt 是 COLLUSION_SYSTEM_PROMPT（无占位符），各节点共用；
    # 但 LLM Center 校验时不强制要求 system 必须独立模板，按"模板占位符 ⊆ 白名单"原则一并断言。
    ("collusion.tender_format_extract",
     "collusion.tender_format_extract", COLLUSION_SYSTEM_PROMPT, COLLUSION_TENDER_FORMAT_PROMPT),
    ("collusion.route_extract",
     "collusion.route_extract", COLLUSION_SYSTEM_PROMPT, COLLUSION_EXTRACT_ROUTE_PROMPT),
    ("collusion.pair_compare_entity",
     "collusion.pair_compare_entity", COLLUSION_SYSTEM_PROMPT, COLLUSION_ENTITY_PROMPT),
    ("collusion.pair_compare_text",
     "collusion.pair_compare_text", COLLUSION_SYSTEM_PROMPT, COLLUSION_TEXT_PATTERN_PROMPT),
    ("collusion.pair_compare_business",
     "collusion.pair_compare_business", COLLUSION_SYSTEM_PROMPT, COLLUSION_BUSINESS_PROMPT),
    ("collusion.single_dim_search",
     "collusion.single_dim_search", COLLUSION_SYSTEM_PROMPT, COLLUSION_SINGLE_DIM_PROMPT),
    ("collusion.pair_judgement",
     "collusion.pair_judgement", COLLUSION_SYSTEM_PROMPT, COLLUSION_PAIR_JUDGEMENT_PROMPT),
    ("collusion.group_analysis",
     "collusion.group_analysis", COLLUSION_SYSTEM_PROMPT, COLLUSION_GROUP_ANALYSIS_PROMPT),
    # project_summary 与 overall_synthesis 复用同一 user 模板（见 synthesis_stage.py）
    ("collusion.project_summary",
     "collusion.project_summary", COLLUSION_SYSTEM_PROMPT, COLLUSION_PROJECT_SUMMARY_PROMPT),
    ("collusion.overall_synthesis",
     "collusion.overall_synthesis", COLLUSION_SYSTEM_PROMPT, COLLUSION_PROJECT_SUMMARY_PROMPT),
    ("collusion.industry_signal",
     "collusion.industry_signal", "", COLLUSION_INDUSTRY_SIGNAL_PROMPT),
    ("collusion.industry_profile_gen",
     "collusion.industry_profile_gen", "", COLLUSION_INDUSTRY_PROFILE_PROMPT_V2),
]


@pytest.mark.parametrize(
    "node_key,system_template,user_template",
    [(case[1], case[2], case[3]) for case in _ALIGNMENT_CASES],
    ids=[case[0] for case in _ALIGNMENT_CASES],
)
def test_param_def_input_covers_prompt_placeholders(
    node_key: str, system_template: str, user_template: str
) -> None:
    """ 契约：节点 input 白名单必须覆盖 system+user 模板所有占位符。"""
    whitelist = _build_input_whitelist(node_key)
    template_placeholders = _extract_placeholders(system_template) | _extract_placeholders(
        user_template
    )

    missing = template_placeholders - whitelist
    assert not missing, (
        f" 回潮防御：节点 {node_key} 的 input 白名单缺少占位符 {missing}。"
        f"\n  模板实际占位符: {sorted(template_placeholders)}"
        f"\n  当前白名单:       {sorted(whitelist)}"
        f"\n  请在 scripts/seed_llm_registry.py 的 PARAM_DEFS 中补齐对应 ParamDef，"
        "然后跑 `python -m scripts.seed_llm_registry` 把改动落到数据库。"
    )


# ---------------------------------------------------------------------------
# bid_review 修复锚点（第一阶段：投标审查）
# ---------------------------------------------------------------------------


def test_bid_extract_match_input_whitelist_minimum_set() -> None:
    """显式断言 bid_extract_match 白名单包含本次新增的 is_mandatory。"""
    whitelist = _build_input_whitelist("bid_review.bid_extract_match")
    assert "is_mandatory" in whitelist, (
        "bid_extract_match 缺少 is_mandatory； 投标审查段修复未生效"
    )


def test_review_item_input_whitelist_minimum_set() -> None:
    """显式断言 review_item 白名单包含本次新增的 9 个 input 占位符。"""
    whitelist = _build_input_whitelist("bid_review.review_item")
    new_fields = {
        "response_id",
        "evidence_type",
        "source_pages",
        "completeness",
        "missing_elements",
        "linked_to",
        "evidence_anchor",
        "relevance_score",
        "relevance_note",
    }
    missing = new_fields - whitelist
    assert not missing, (
        f"review_item input 白名单缺少 修复字段: {missing}"
    )


def test_review_no_response_input_whitelist_unchanged() -> None:
    """review_no_response 占位符早就对齐，不应被本次修改破坏。"""
    whitelist = _build_input_whitelist("bid_review.review_no_response")
    no_response_placeholders = _extract_placeholders(NO_RESPONSE_PROMPT)
    missing = no_response_placeholders - whitelist
    assert not missing, (
        f"review_no_response 白名单出现非预期缺失: {missing}"
    )


# ---------------------------------------------------------------------------
# collusion 修复锚点（第二阶段：串标审查）
# ---------------------------------------------------------------------------


def test_collusion_pair_compare_entity_minimum_set() -> None:
    """显式断言 pair_compare_entity 白名单覆盖核心占位符（截图直接报错的 similarity_pct 等）。"""
    whitelist = _build_input_whitelist("collusion.pair_compare_entity")
    new_fields = {
        "doc_a_id", "doc_a_name", "doc_b_id", "doc_b_name",
        "similarity_pct", "coverage_pct", "max_continuous_span",
        "structured_a", "structured_b", "context_a", "context_b",
    }
    missing = new_fields - whitelist
    assert not missing, (
        f"pair_compare_entity input 白名单缺少 修复字段: {missing}"
    )


def test_collusion_pair_judgement_minimum_set() -> None:
    """显式断言 pair_judgement 白名单含 pair_facts（不再用旧名 comparison_facts）+ doc_a_name 等。"""
    whitelist = _build_input_whitelist("collusion.pair_judgement")
    assert "pair_facts" in whitelist, "pair_judgement 缺少 pair_facts"
    assert "doc_a_name" in whitelist, "pair_judgement 缺少 doc_a_name（截图报错锚点）"
    # 旧名应该已从 PARAM_DEFS 中删除（seed 软降级会处理 DB 侧）
    assert "comparison_facts" not in whitelist, (
        "pair_judgement 仍含已废弃的 comparison_facts；应已被新名 pair_facts 替换"
    )


def test_collusion_group_analysis_minimum_set() -> None:
    """group_analysis 白名单含 aggregated_evidence/global_findings/member_list（不再用旧名 group_pairs）。"""
    whitelist = _build_input_whitelist("collusion.group_analysis")
    for required in ("aggregated_evidence", "global_findings", "member_list"):
        assert required in whitelist, f"group_analysis 缺少 {required}"
    assert "group_pairs" not in whitelist, (
        "group_analysis 仍含已废弃的 group_pairs；应已被新名替换"
    )


def test_collusion_tender_format_extract_uses_tender_text() -> None:
    """tender_format_extract 白名单应使用模板实际占位符 tender_text（不再用旧名 tender_content）。"""
    whitelist = _build_input_whitelist("collusion.tender_format_extract")
    assert "tender_text" in whitelist
    assert "tender_content" not in whitelist, (
        "tender_format_extract 仍含已废弃的 tender_content；应已被新名 tender_text 替换"
    )


# ---------------------------------------------------------------------------
# 完整性辅助：所有 ALIGNMENT_CASES 的节点都应有至少一个 input 参数注册
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node_key",
    [case[1] for case in _ALIGNMENT_CASES],
    ids=[case[0] for case in _ALIGNMENT_CASES],
)
def test_seed_param_defs_define_node_input(node_key: str) -> None:
    """确保 PARAM_DEFS 至少为每个 ALIGNMENT_CASES 节点注册了 1 个 input 参数。"""
    whitelist = _build_input_whitelist(node_key)
    assert whitelist, f"PARAM_DEFS 中节点 {node_key} 无任何 input 参数注册"
