""" 防御测试：行业画像分节解析与按场景选段。

风险映射（来自生产库 writing_clauses 实测）：
- 画像真实长度 1781~3878 字符，第 4 节起始偏移 1610~1995；原 ``content[:1500]``
 头部截断使 14/15 项目丢失"评标专家核心关注点/写作风格指导/差异化竞争建议"三节
- 存在人工扩写至 5156 字符的画像，以及失败兜底文案（项目 515 形态）
- 术语表为编号列表，实测单项目 30 条
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.industry_profile_view import (
    PURPOSE_CHAPTER_PROFILE,
    PURPOSE_DATA_FILL,
    PURPOSE_FREE_WRITING,
    PURPOSE_SUMMARY,
    SECTION_DIFFERENTIATION,
    SECTION_REVIEWER_FOCUS,
    SECTION_TECHNICAL_STRUCTURE,
    SECTION_TERMINOLOGY,
    build_profile_excerpt,
    extract_profile_terminology,
    parse_industry_profile,
    select_profile_sections,
)

_HEAD_1 = "### 1. 行业识别与典型特征"
_HEAD_2 = "### 2. 专业术语表（20-30个）"
_HEAD_3 = "### 3. 技术方案典型结构"
_HEAD_4 = "### 4. 评标专家核心关注点（5-8个）"
_HEAD_5 = "### 5. 写作风格指导"
_HEAD_6 = "### 6. 差异化竞争建议"


def _filled_section(heading: str, total_chars: int, marker: str) -> str:
    """构造指定总长的画像节，正文含可断言的标记词。"""
    body = f"\n{marker}"
    padding = max(total_chars - len(heading) - len(body), 0)
    return heading + body + ("论述填充内容" * (padding // 6 + 1))[:padding]


@pytest.fixture
def realistic_profile() -> str:
    """复刻生产库项目 2247 的段落长度分布（总长约 3040，第 4 节起始 1721）。"""
    return "\n\n".join(
        [
            _filled_section(_HEAD_1, 600, "行业特征标记"),
            _filled_section(_HEAD_2, 370, "术语表标记"),
            _filled_section(_HEAD_3, 740, "技术结构标记"),
            _filled_section(_HEAD_4, 440, "评标关注点标记"),
            _filled_section(_HEAD_5, 400, "写作风格标记"),
            _filled_section(_HEAD_6, 470, "差异化建议标记"),
        ]
    )


class TestParseIndustryProfile:

    def test_six_sections_parsed(self, realistic_profile: str):
        view = parse_industry_profile(realistic_profile)
        assert view.parsed is True
        assert len(view.sections) == 6
        assert "评标关注点标记" in view.section(SECTION_REVIEWER_FOCUS)
        assert "差异化建议标记" in view.section(SECTION_DIFFERENTIATION)

    def test_section_keeps_own_heading(self, realistic_profile: str):
        """选段文本必须自带标题，否则 LLM 无法判断段落语义。"""
        view = parse_industry_profile(realistic_profile)
        assert view.section(SECTION_REVIEWER_FOCUS).startswith("### 4.")

    def test_plain_ordinal_headings_supported(self):
        content = "1. 行业识别\n特征内容\n\n2. 专业术语表\n1. 多相流\n2. 缓蚀剂"
        view = parse_industry_profile(content)
        assert view.parsed is True
        assert "特征内容" in view.section("industry_traits")

    def test_chinese_ordinal_headings_supported(self):
        content = "一、行业识别\n特征内容\n\n二、专业术语表\n- 多相流\n- 缓蚀剂"
        view = parse_industry_profile(content)
        assert view.parsed is True
        assert "多相流" in view.section(SECTION_TERMINOLOGY)

    def test_duplicate_ordinal_keeps_first_occurrence(self):
        """正文内引用行不得覆盖真实标题，否则该节内容被腰斩。"""
        content = (
            "### 1. 行业识别\n首个正文\n\n"
            "### 2. 专业术语表\n- 多相流\n"
            "1. 这是术语表内部的编号条目\n"
        )
        view = parse_industry_profile(content)
        assert "首个正文" in view.section("industry_traits")

    @pytest.mark.parametrize(
        "content",
        [
            None,
            "",
            "   \n  ",
            "（行业画像生成失败，请手动补充）",
            "参考信息不足，无法生成可靠行业画像。请先完成约束提取或补充有效招标信息。",
        ],
    )
    def test_unparseable_content_returns_not_parsed(self, content):
        view = parse_industry_profile(content)
        assert view.parsed is False
        assert view.sections == {}

    def test_single_markdown_heading_is_usable(self):
        """人工删节后只剩一节仍应可用，比退回头部截断更精确。"""
        view = parse_industry_profile("### 1. 行业识别\n只有一节内容")
        assert view.parsed is True
        assert "只有一节内容" in view.section("industry_traits")

    def test_terminology_numbering_not_mistaken_for_headings(self):
        """生产画像术语表为 1.~30. 编号，误判为节标题会把画像腰斩。"""
        terms = "\n".join(f"{i}. 术语{i}" for i in range(1, 31))
        content = (
            "### 1. 行业识别与典型特征\n行业特征标记\n\n"
            f"### 2. 专业术语表（20-30个）\n{terms}\n\n"
            "### 3. 技术方案典型结构\n技术结构标记\n\n"
            "### 4. 评标专家核心关注点\n评标关注点标记\n"
        )
        view = parse_industry_profile(content)
        assert len(view.sections) == 4
        assert "评标关注点标记" in view.section(SECTION_REVIEWER_FOCUS)
        assert "技术结构标记" in view.section(SECTION_TECHNICAL_STRUCTURE)
        assert "术语30" in view.section(SECTION_TERMINOLOGY)

    def test_plain_ordinal_requires_title_keyword(self):
        """无井号形态必须靠关键词校验，否则任意编号列表都会被当成节标题。"""
        view = parse_industry_profile(
            "1. 腐蚀速率\n2. 缓蚀剂\n3. 多相流\n4. 阴极保护\n"
        )
        assert view.parsed is False


class TestChapterProfileSelection:
    """ 核心用例：1500 字符预算下不得丢失评标关注点。"""

    def test_head_slice_loses_reviewer_focus(self, realistic_profile: str):
        """复现原缺陷：头部截断在真实长度分布下拿不到第 4 节。"""
        assert _HEAD_4 not in realistic_profile[:1500]

    def test_selection_keeps_reviewer_focus_within_same_budget(
        self, realistic_profile: str
    ):
        excerpt = build_profile_excerpt(
            realistic_profile,
            purpose=PURPOSE_CHAPTER_PROFILE,
            max_chars=1500,
        )
        assert "评标关注点标记" in excerpt
        assert "技术结构标记" in excerpt
        assert len(excerpt) <= 1500

    def test_selection_drops_lowest_priority_first(self, realistic_profile: str):
        """预算不足时牺牲的是场景内低优先级段落，而非物理位置靠后的段落。"""
        excerpt = build_profile_excerpt(
            realistic_profile,
            purpose=PURPOSE_CHAPTER_PROFILE,
            max_chars=1500,
        )
        assert "写作风格标记" not in excerpt
        assert "差异化建议标记" not in excerpt


class TestFreeWritingSelection:

    def test_differentiation_survives_3000_budget(self, realistic_profile: str):
        """自由论述预算 3000，差异化建议属加分项来源，不得被尾部截断吃掉。"""
        excerpt = build_profile_excerpt(
            realistic_profile,
            purpose=PURPOSE_FREE_WRITING,
            max_chars=3000,
        )
        assert "差异化建议标记" in excerpt
        assert "评标关注点标记" in excerpt
        assert "写作风格标记" in excerpt
        assert len(excerpt) <= 3000

    def test_oversized_profile_keeps_top_priority(self):
        """人工扩写至 5000+ 字符时仍须保住最高优先级段落。"""
        oversized = "\n\n".join(
            [
                _filled_section(_HEAD_1, 1200, "行业特征标记"),
                _filled_section(_HEAD_2, 900, "术语表标记"),
                _filled_section(_HEAD_3, 1400, "技术结构标记"),
                _filled_section(_HEAD_4, 700, "评标关注点标记"),
                _filled_section(_HEAD_5, 500, "写作风格标记"),
                _filled_section(_HEAD_6, 500, "差异化建议标记"),
            ]
        )
        assert len(oversized) > 5000
        excerpt = build_profile_excerpt(
            oversized, purpose=PURPOSE_FREE_WRITING, max_chars=3000
        )
        assert "评标关注点标记" in excerpt
        assert len(excerpt) <= 3000


class TestDataFillAndSummarySelection:

    def test_data_fill_only_takes_terminology_and_style(
        self, realistic_profile: str
    ):
        excerpt = build_profile_excerpt(
            realistic_profile, purpose=PURPOSE_DATA_FILL, max_chars=1200
        )
        assert "术语表标记" in excerpt
        assert "写作风格标记" in excerpt
        assert "差异化建议标记" not in excerpt
        assert "技术结构标记" not in excerpt

    def test_summary_purpose_excludes_advisory_sections(
        self, realistic_profile: str
    ):
        """摘要注入建议性内容会让画像建议以'前文事实'身份传播，必须只给术语表。"""
        excerpt = build_profile_excerpt(
            realistic_profile, purpose=PURPOSE_SUMMARY, max_chars=1200
        )
        assert "术语表标记" in excerpt
        assert "差异化建议标记" not in excerpt
        assert "评标关注点标记" not in excerpt


class TestBudgetBoundaries:

    def test_unparseable_falls_back_to_head_slice(self):
        content = "（行业画像生成失败，请手动补充）"
        excerpt = build_profile_excerpt(
            content, purpose=PURPOSE_FREE_WRITING, max_chars=10
        )
        assert excerpt == content[:10]

    def test_tiny_budget_produces_no_fragment(self, realistic_profile: str):
        """预算小于碎片阈值时不得塞入无信息量的片段。"""
        excerpt = select_profile_sections(
            parse_industry_profile(realistic_profile),
            purpose=PURPOSE_CHAPTER_PROFILE,
            max_chars=50,
        )
        assert excerpt == ""

    def test_partial_tail_can_be_disabled(self, realistic_profile: str):
        view = parse_industry_profile(realistic_profile)
        excerpt = select_profile_sections(
            view,
            purpose=PURPOSE_CHAPTER_PROFILE,
            max_chars=1500,
            allow_partial_tail=False,
        )
        for body in excerpt.split("\n\n"):
            assert body.strip() in [
                view.section(key).strip() for key in view.sections
            ]

    @pytest.mark.parametrize("budget", [0, -1])
    def test_non_positive_budget_returns_empty(
        self, realistic_profile: str, budget: int
    ):
        assert (
            build_profile_excerpt(
                realistic_profile,
                purpose=PURPOSE_FREE_WRITING,
                max_chars=budget,
            )
            == ""
        )

    def test_unknown_purpose_falls_back_to_outline_order(
        self, realistic_profile: str
    ):
        excerpt = build_profile_excerpt(
            realistic_profile, purpose="unknown_purpose", max_chars=1500
        )
        assert "行业特征标记" in excerpt


class TestTerminologyExtraction:

    def test_numbered_terminology_extracted(self):
        content = (
            "### 1. 行业识别\n特征内容\n\n"
            "### 2. 专业术语表（20-30个）\n"
            "1. 多相流\n"
            "2. 电化学行为\n"
            "3. 腐蚀速率\n"
            "4. 电感耦合等离子体发射光谱仪（ICP）\n"
            "5. HSE管理体系\n"
        )
        terms = extract_profile_terminology(content)
        assert terms[:3] == ["多相流", "电化学行为", "腐蚀速率"]
        assert "电感耦合等离子体发射光谱仪（ICP）" in terms
        assert "HSE管理体系" in terms

    def test_bullet_terminology_extracted(self):
        content = (
            "### 1. 行业识别\n特征\n\n"
            "### 2. 专业术语表\n- 缓蚀剂\n* 阴极保护\n• 界面腐蚀\n"
        )
        terms = extract_profile_terminology(content)
        assert set(terms) == {"缓蚀剂", "阴极保护", "界面腐蚀"}

    def test_terminology_not_taken_from_other_sections(self):
        """技术结构节的长句不得被当作术语，否则一致性检查将大面积误报。"""
        content = (
            "### 2. 专业术语表\n1. 多相流\n\n"
            "### 3. 技术方案典型结构\n"
            "- **第一章 项目概述与理解**：深入分析腐蚀环境特点并阐述战略意义\n"
        )
        terms = extract_profile_terminology(content)
        assert terms == ["多相流"]

    def test_terminology_strips_emphasis_and_explanation(self):
        content = (
            "### 2. 专业术语表\n"
            "1. **多相流**\n"
            "2. 缓蚀剂：用于降低腐蚀速率的化学药剂\n"
            "3. 阴极保护 - 通过外加电流抑制腐蚀\n"
        )
        terms = extract_profile_terminology(content)
        assert terms == ["多相流", "缓蚀剂", "阴极保护"]

    def test_overlong_entry_rejected(self):
        content = (
            "### 2. 专业术语表\n"
            "1. 多相流\n"
            f"2. {'超长描述内容' * 12}\n"
        )
        terms = extract_profile_terminology(content)
        assert terms == ["多相流"]

    def test_duplicate_terms_deduped_keeping_order(self):
        content = "### 2. 专业术语表\n1. 多相流\n2. 缓蚀剂\n3. 多相流\n"
        assert extract_profile_terminology(content) == ["多相流", "缓蚀剂"]

    def test_limit_respected(self):
        entries = "\n".join(f"{i}. 术语{i}" for i in range(1, 40))
        content = f"### 2. 专业术语表\n{entries}\n"
        assert len(extract_profile_terminology(content, limit=10)) == 10

    @pytest.mark.parametrize(
        "content",
        [None, "", "（行业画像生成失败，请手动补充）"],
    )
    def test_missing_terminology_section_returns_empty(self, content):
        assert extract_profile_terminology(content) == []

    def test_terminology_absent_but_other_sections_present(self):
        content = "### 1. 行业识别\n特征\n\n### 3. 技术方案典型结构\n结构\n"
        assert extract_profile_terminology(content) == []
