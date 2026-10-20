"""写标约束提取质量提升契约测试。

覆盖 Prompt 自洽、结构化元数据、目录事实字段和三层提取运行参数。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.writer import clause_extractor as extractor_mod
from app.core.writer.clause_extractor import ClauseExtractor
from app.core.writer.prompts.clause_extract import (
    BID_DIRECTORY_EXTRACT_SYSTEM,
    BID_DIRECTORY_EXTRACT_USER,
    HARD_CONSTRAINT_SYSTEM,
    HARD_CONSTRAINT_USER,
    STRUCTURE_CONSTRAINT_SYSTEM,
    get_bid_directory_extract_prompt,
    get_bid_structure_analysis_prompt,
    get_dedup_decision_prompt,
    get_generation_guide_prompt,
    get_hard_constraint_prompt,
    get_render_profile_prompt,
    get_structure_constraint_prompt,
)
from app.core.writer.prompts.clause_extract_unified import (
    get_unified_clause_extract_prompt,
)
from app.schemas.writing import (
    BidDirectoryItem,
    ExtractedClause,
    RenderProfileParsed,
)


class _FakeJsonClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def test_extracted_clause_accepts_downstream_metadata() -> None:
    clause = ExtractedClause(
        sub_type="scoring_criterion",
        content="技术方案完整性最高得5分",
        raw_text="技术方案完整性：最高得5分",
        source_page=12,
        score_weight=5,
        metadata={
            "score_item_name": "技术方案完整性",
            "max_score": 5,
            "score_rules": ["覆盖全部技术指标得5分"],
            "target_section_hint": "技术方案",
            "evidence_hint": "逐项响应技术指标",
        },
    )

    assert clause.metadata is not None
    assert clause.metadata["target_section_hint"] == "技术方案"


def test_directory_item_keeps_fact_fields_separate_from_type_hint() -> None:
    item = BidDirectoryItem(
        order=1,
        title="二-2-1 资格证明材料",
        node_type="data_fill",
        node_type_source="llm_suggestion",
        source_span="第七章电子投标文件格式",
    )

    assert item.title == "二-2-1 资格证明材料"
    assert item.node_type == "data_fill"
    assert item.node_type_source == "llm_suggestion"
    assert item.source_span == "第七章电子投标文件格式"


def test_render_profile_can_carry_submission_requirements() -> None:
    parsed = RenderProfileParsed(
        font_body="仿宋",
        submission_requirements={
            "binding": "胶装成册，不得活页",
            "seal_signature": "逐页加盖公章并加盖骑缝章",
            "blind_bid": "技术标不得出现投标人名称",
        },
    )

    assert parsed.submission_requirements
    assert "骑缝章" in parsed.submission_requirements["seal_signature"]


def test_prompt_contracts_keep_fact_extraction_auditable() -> None:
    assert "metadata" in HARD_CONSTRAINT_USER
    assert "risk_consequence" in HARD_CONSTRAINT_USER
    assert "target_section_hint" in HARD_CONSTRAINT_USER
    assert "node_type_source" in BID_DIRECTORY_EXTRACT_SYSTEM
    assert "source_span" in BID_DIRECTORY_EXTRACT_SYSTEM
    assert "只用于定位" in BID_DIRECTORY_EXTRACT_SYSTEM
    assert "目录」页" in BID_DIRECTORY_EXTRACT_SYSTEM
    assert "禁止写入 items" in BID_DIRECTORY_EXTRACT_USER


def test_hard_prompt_enforces_writability_principle() -> None:
    """硬约束 Prompt 必须承载可写性判定原则、行为性废标排除与★条款指令。"""
    # 可写性判定原则
    assert "能否通过投标文件的内容编写" in HARD_CONSTRAINT_SYSTEM
    # disqualification 收窄为内容性
    assert "内容性废标" in HARD_CONSTRAINT_SYSTEM
    # 行为性违规示例(不提取)
    assert "串通投标" in HARD_CONSTRAINT_SYSTEM
    # ★/▲ 实质性条款指令
    assert "★" in HARD_CONSTRAINT_SYSTEM
    assert "实质性" in HARD_CONSTRAINT_SYSTEM


def test_structure_prompt_promotes_content_required_extraction() -> None:
    """结构约束 Prompt 必须强化 content_required/directory_rule 的写作导向与拆分指令。"""
    assert "写作依据" in STRUCTURE_CONSTRAINT_SYSTEM
    assert "逐条提取" in STRUCTURE_CONSTRAINT_SYSTEM
    assert "拆细" in STRUCTURE_CONSTRAINT_SYSTEM


def test_writer_prompt_factories_format_without_keyerror() -> None:
    """所有写标 Prompt 工厂函数必须能完成本地格式化。

    该测试防止 JSON 示例里的字面大括号再次被 `.format()` 当成占位符。
    """
    from app.core.ai.critic.prompts.judge_templates import JUDGE_WRITER_USER
    from app.core.writer.prompts.chapter_profile import get_chapter_profile_prompt
    from app.core.writer.prompts.clause_classify import get_clause_classify_prompt
    from app.core.writer.prompts.image_classify import get_image_classify_prompt
    from app.core.writer.prompts.industry_profile import get_industry_profile_prompt
    from app.core.writer.prompts.material_match import get_chapter_judge_prompt
    from app.core.writer.prompts.material_tag import get_material_tag_prompt
    from app.core.writer.prompts.outline_generate import (
        get_outline_generate_prompt,
        get_section_fill_prompt,
        get_skeleton_enrich_prompt,
    )
    from app.core.writer.prompts.section_compose import (
        get_cross_ref_check_prompt,
        get_data_fill_prompt,
        get_free_writing_prompt,
        get_summarize_prompt,
    )

    cases = [
        get_hard_constraint_prompt("招标内容", 1, 2),
        get_structure_constraint_prompt("招标内容", 1, 2),
        get_generation_guide_prompt("招标内容", 1, 2),
        get_unified_clause_extract_prompt("招标内容", 1, 2),
        get_render_profile_prompt("格式要求"),
        get_bid_structure_analysis_prompt(
            directory_rules="目录规则",
            scoring_criteria="评分标准",
            content_requirements="内容要求",
            material_requirements="材料要求",
            industry_profile="行业画像",
            hard_structure_signals="固定模板线索",
        ),
        get_dedup_decision_prompt("[]", 0),
        get_bid_directory_extract_prompt("目录相关原文"),
        get_industry_profile_prompt(
            project_context="项目背景",
            scoring_summary="评分标准",
            content_requirements="内容要求",
            material_requirements="材料要求",
            differentiation_hints="差异化提示",
            directory_rules="目录规则",
            structured_signals="结构化信号",
        ),
        get_outline_generate_prompt(
            directory_rules="目录规则",
            content_requirements="内容要求",
            material_requirements="材料要求",
            scoring_criteria="评分标准",
            industry_profile="行业画像",
        ),
        get_section_fill_prompt(
            section_title="技术标",
            section_category="technical",
            relevant_dir_rules="目录规则",
            relevant_content_reqs="内容要求",
            relevant_scoring="评分标准",
            relevant_materials="材料要求",
            industry_profile="行业画像",
        ),
        get_skeleton_enrich_prompt(
            node_title="技术方案",
            node_order=1,
            volume_title="技术标",
            relevant_scoring="评分标准",
            relevant_content_reqs="内容要求",
            relevant_materials="材料要求",
            project_context="项目背景",
            sibling_titles_text="商务响应",
        ),
        get_material_tag_prompt(
            title="公司简介",
            material_type="text",
            content_preview="公司简介内容",
            outline_tree="技术方案",
            resolved_label="企业概况",
        ),
        get_material_tag_prompt(
            title="证书图片",
            material_type="image",
            content_preview="营业执照",
            outline_tree="资格证明",
            resolved_label="企业资质",
        ),
        get_material_tag_prompt(
            title="人员表",
            material_type="table",
            content_preview="项目经理",
            outline_tree="人员配置",
            resolved_label="人员配置",
        ),
        get_image_classify_prompt(
            material_title="证书图片",
            image_index=1,
            width=100,
            height=200,
            vl_content="营业执照",
        ),
        get_chapter_judge_prompt(
            chapter_title="技术方案",
            chapter_type="free_writing",
            chapter_path="技术标/技术方案",
            semantic_role="技术论述",
            expects="技术措施",
            rejects="报价",
            candidates_text="素材候选",
        ),
        get_clause_classify_prompt(
            clauses_text="约束列表",
            outline_candidates_text="候选章节",
        ),
        get_chapter_profile_prompt(
            tree_text="目录树",
            leaf_list="叶子章节",
            industry_profile_excerpt="行业画像",
            clause_domain_summary="约束摘要",
            clause_examples_summary="约束示例",
        ),
        get_data_fill_prompt(
            node_title="人员配置",
            target_word_count=500,
            clauses_text="约束",
            materials_text="素材",
            chapter_profile_text="章节画像",
            chapter_number="一、",
            sub_numbering_hint="子标题使用 1、2、3",
            material_usage_rules="优先使用素材",
        ),
        get_free_writing_prompt(
            node_title="技术方案",
            target_word_count=1500,
            clauses_text="约束",
            materials_text="素材",
            previous_summaries_text="前文摘要",
            industry_profile_excerpt="行业画像",
            chapter_profile_text="章节画像",
            chapter_number="二、",
            sub_numbering_hint="子标题使用 1、2、3",
            material_usage_rules="优先使用素材",
        ),
        get_summarize_prompt("技术方案", "章节正文"),
        get_cross_ref_check_prompt("目录", "引用片段"),
        ("SYS", JUDGE_WRITER_USER.format(
            context_summary="上下文",
            original_output="输出",
            evidence_text="证据",
        )),
    ]

    for system, user in cases:
        assert isinstance(system, str)
        assert isinstance(user, str)
        assert user.strip()


def test_bid_structure_prompt_consumes_hard_structure_signals() -> None:
    _system, user = get_bid_structure_analysis_prompt(
        directory_rules="技术标、商务标目录",
        scoring_criteria="技术方案 40 分",
        content_requirements="技术方案应包含实施计划",
        material_requirements="提供营业执照扫描件",
        industry_profile="检测服务类项目",
        hard_structure_signals="电子投标文件由资格证明、报价部分、技术商务部分组成",
    )

    assert "hard_structure_signals" not in user
    assert "资格证明、报价部分、技术商务部分" in user


@pytest.mark.asyncio
async def test_extract_batch_uses_registry_seed_token_budget() -> None:
    fake_client = _FakeJsonClient([
        {
            "sub_type": "project_context",
            "content": "项目地点为园区内",
            "raw_text": "项目地点为园区内",
            "source_page": 3,
            "score_weight": None,
        }
    ])
    extractor = object.__new__(ClauseExtractor)
    extractor.llm_client = fake_client

    batch = [
        SimpleNamespace(
            content="项目地点为园区内，服务期一年。",
            page_num=3,
            chunk_index=0,
        )
    ]

    result = await extractor._extract_batch(
        batch=batch,
        prompt_fn=lambda content, page_start, page_end: ("SYS", content),
        layer="generation_guide",
        all_chunks=batch,
    )

    assert len(result) == 1
    assert fake_client.calls[0]["max_tokens"] == extractor_mod._EXTRACT_BATCH_MAX_TOKENS == 4000


@pytest.mark.asyncio
async def test_extract_layer_surfaces_prompt_format_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prompt 构建错误必须让任务失败，不能被吞成空提取结果。"""
    extractor = object.__new__(ClauseExtractor)
    extractor.concurrency = 1
    extractor._report_progress = lambda *args, **kwargs: None

    def _bad_prompt(_content: str, _page_start: int, _page_end: int):
        raise KeyError("score_item_name")

    monkeypatch.setitem(
        extractor_mod._LAYER_PROMPT_MAP,
        "generation_guide",
        _bad_prompt,
    )

    batch = [
        SimpleNamespace(
            content="评分标准：技术方案完整性最高得5分。",
            page_num=3,
            chunk_index=0,
        )
    ]

    with pytest.raises(KeyError):
        await extractor._extract_layer(
            layer="generation_guide",
            batches=[batch],
            total_batches=1,
            all_chunks=None,
        )
