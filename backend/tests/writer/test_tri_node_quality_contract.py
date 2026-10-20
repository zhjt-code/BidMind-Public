"""写标约束分类、行业画像、目录生成三节点质量契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.writer import outline_generator as outline_mod
from app.core.writer.industry_profiler import IndustryProfiler
from app.core.writer.material_matcher import MaterialMatcher
from app.core.writer.prompts.clause_classify import get_clause_classify_prompt
from app.core.writer.prompts.industry_profile import get_industry_profile_prompt
from app.core.writer.prompts.outline_generate import (
    OUTLINE_GENERATE_SYSTEM,
    SECTION_FILL_SYSTEM,
    SKELETON_ENRICH_SYSTEM,
    get_section_fill_prompt,
    get_skeleton_enrich_prompt,
)
from scripts.seed_llm_registry import PARAM_DEFS


def test_clause_classify_prompt_consumes_outline_candidates() -> None:
    _system, user = get_clause_classify_prompt(
        clauses_text="- ID=1, 类型=fixed_template, 内容=投标函应按模板填写",
        outline_candidates_text="- 章节: 投标函 | node_type=fixed_template | volume=commercial",
    )

    assert "投标函" in user
    assert "outline_candidates_text" not in user
    assert "候选章节" in user
    assert "target_hint" in user


def test_material_matcher_formats_outline_candidates() -> None:
    nodes = [
        SimpleNamespace(id=1, title="商务标", depth=0, parent_id=None, node_type="free_writing", volume_type="commercial"),
        SimpleNamespace(id=2, title="投标函", depth=1, parent_id=1, node_type="fixed_template", volume_type="commercial"),
        SimpleNamespace(id=3, title="技术方案", depth=1, parent_id=1, node_type="free_writing", volume_type="technical"),
    ]

    text = MaterialMatcher._format_outline_candidates(nodes)

    assert "候选分卷: 商务标" in text
    assert "候选章节: 投标函" in text
    assert "volume_type=commercial" in text


def test_industry_profile_prompt_consumes_additional_fact_inputs() -> None:
    _system, user = get_industry_profile_prompt(
        project_context="项目为园区物业服务",
        scoring_summary="服务方案 20 分",
        content_requirements="方案需覆盖人员配置",
        material_requirements="提供业绩证明",
        differentiation_hints="鼓励智慧物业平台",
        directory_rules="技术标包含服务方案",
        structured_signals="metadata={target_section_hint:服务方案}",
    )

    assert "鼓励智慧物业平台" in user
    assert "技术标包含服务方案" in user
    assert "metadata=" in user
    assert "招标事实" in user
    assert "industry_profile_output_contract:v1" in user
    assert "禁止手写可见编号" in user


def test_industry_profiler_build_context_includes_hints_and_metadata() -> None:
    profiler = object.__new__(IndustryProfiler)
    clauses = [
        SimpleNamespace(sub_type="differentiation_hint", content="鼓励智慧物业平台", score_weight=None, metadata_json=None),
        SimpleNamespace(sub_type="directory_rule", content="技术标包含服务方案", score_weight=None, metadata_json=None),
        SimpleNamespace(
            sub_type="scoring_criterion",
            content="服务方案 20 分",
            score_weight=20,
            metadata_json={"target_section_hint": "服务方案", "max_score": 20},
        ),
    ]

    context = profiler._build_context(clauses)

    assert "鼓励智慧物业平台" in context["differentiation_hints"]
    assert "技术标包含服务方案" in context["directory_rules"]
    assert "target_section_hint" in context["structured_signals"]


def test_outline_prompts_contain_no_fabrication_boundary() -> None:
    assert "未被招标约束支撑" in OUTLINE_GENERATE_SYSTEM
    _, section_user = get_section_fill_prompt(
        section_title="资格标",
        section_category="qualification",
        relevant_dir_rules="资格标包含资格证明材料",
        relevant_content_reqs="",
        relevant_scoring="",
        relevant_materials="营业执照扫描件",
        industry_profile="",
        structure_mode="qual_tech_comm",
    )
    assert "未被招标约束支撑" in section_user


def test_section_fill_policy_is_structure_mode_driven() -> None:
    system, user = get_section_fill_prompt(
        section_title="资格标",
        section_category="qualification",
        relevant_dir_rules="",
        relevant_content_reqs="",
        relevant_scoring="",
        relevant_materials="",
        industry_profile="",
        structure_mode="qual_tech_comm",
    )

    assert "资格标" in user
    assert "只包含技术标和商务标两个分卷" not in system


def test_registry_system_prompts_do_not_require_runtime_formatting() -> None:
    assert "{section_policy}" not in SECTION_FILL_SYSTEM
    assert "{section_title}" not in SECTION_FILL_SYSTEM
    assert "{section_description}" not in SECTION_FILL_SYSTEM
    assert "{node_title}" not in SKELETON_ENRICH_SYSTEM


def test_section_fill_seed_declares_all_user_placeholders() -> None:
    section_fill_params = {
        param.param_key
        for param in PARAM_DEFS
        if param.node_key == "writing.section_fill"
    }

    assert {
        "section_title",
        "section_category",
        "section_description",
        "structure_mode",
        "section_policy",
    }.issubset(section_fill_params)


@pytest.mark.asyncio
async def test_outline_runtime_params_are_fact_extraction_oriented(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_chat_json_with_input_guard(self, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(
        outline_mod.OutlineGenerator,
        "_chat_json_with_input_guard",
        fake_chat_json_with_input_guard,
    )

    generator = object.__new__(outline_mod.OutlineGenerator)
    async def _build_context(_project_id):
        return {
            "directory_rules": "目录规则",
            "content_requirements": "",
            "material_requirements": "",
            "scoring_criteria": "",
            "industry_profile": "",
        }

    generator._build_context = _build_context
    generator._parse_nodes = lambda raw: []
    generator._assign_word_counts_tree = lambda nodes, total: None

    async def _save_tree(_project_id, nodes):
        return nodes

    generator._save_tree = _save_tree

    result = await outline_mod.OutlineGenerator._generate_unified(
        generator,
        project_id=1,
        total_word_budget=1000,
    )

    assert result == []
    assert calls[0]["temperature"] == 0.1
    assert calls[0]["max_tokens"] == 8000


def test_skeleton_enrich_prompt_keeps_existing_signature_and_boundary() -> None:
    _system, user = get_skeleton_enrich_prompt(
        node_title="技术方案",
        node_order=1,
        volume_title="技术标",
        relevant_scoring="技术方案 20 分",
        relevant_content_reqs="方案需包含进度计划",
        relevant_materials="",
        project_context="",
    )

    assert "未被招标约束支撑" in user
    assert "不要把采购需求" in _system
