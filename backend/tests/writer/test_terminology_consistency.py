"""防御测试：行业画像术语表接入术语一致性检查。

风险映射：画像产出 20-30 个行业术语，原术语检查只比对甲乙称谓两组硬编码变体，
术语表完全未被消费。接入时的首要风险是误报刷屏——术语表含仪器型号与标准编号，
正文不可能全覆盖，故只检测客观可判定的"同一术语多种写法"。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.terminology_consistency import (
    ISSUE_TYPE_TERMINOLOGY,
    find_term_variant_issues,
)


class TestCaseVariantDetection:

    def test_mixed_case_reported(self):
        text = "本项目执行 HSE管理体系 要求，并按 hse管理体系 开展月度检查。"
        issues = find_term_variant_issues(text, ["HSE管理体系"])
        assert len(issues) == 1
        assert issues[0]["type"] == ISSUE_TYPE_TERMINOLOGY
        assert issues[0]["severity"] == "low"
        assert "HSE管理体系" in issues[0]["message"]

    def test_case_and_bracket_variant_combined_reported(self):
        """缩写常被写成小写并改括号形式，两类差异叠加时仍须检出。"""
        text = "采用光谱仪（ICP）分析，另有光谱仪(icp)复测。"
        issues = find_term_variant_issues(text, ["光谱仪（ICP）"])
        assert len(issues) == 1
        assert "光谱仪（ICP）" in issues[0]["message"]

    def test_consistent_case_not_reported(self):
        text = "本项目执行 HSE管理体系 要求，全文统一使用 HSE管理体系。"
        assert find_term_variant_issues(text, ["HSE管理体系"]) == []

    def test_pure_chinese_term_skips_case_check(self):
        """纯中文术语无大小写概念，不得因忽略大小写匹配而误报。"""
        text = "缓蚀剂投加量按月核定，缓蚀剂性能每季度复检。"
        assert find_term_variant_issues(text, ["缓蚀剂"]) == []

    def test_term_absent_from_text_not_reported(self):
        """术语未出现属正常情况（术语表含大量型号与标准号），禁止报缺失。"""
        text = "本章说明质量管理流程。"
        assert find_term_variant_issues(text, ["电感耦合等离子体发射光谱仪"]) == []


class TestBracketVariantDetection:

    def test_full_and_half_width_brackets_reported(self):
        text = "采用扫描电子显微镜（SEM）观察，另用扫描电子显微镜(SEM)复检。"
        issues = find_term_variant_issues(text, ["扫描电子显微镜（SEM）"])
        assert len(issues) == 1
        message = issues[0]["message"]
        assert "扫描电子显微镜（SEM）" in message
        assert "扫描电子显微镜(SEM)" in message

    def test_single_bracket_style_not_reported(self):
        text = "采用扫描电子显微镜（SEM）观察，扫描电子显微镜（SEM）结果留档。"
        assert find_term_variant_issues(text, ["扫描电子显微镜（SEM）"]) == []

    def test_term_without_bracket_unaffected(self):
        text = "腐蚀速率按月统计，腐蚀速率台账留存。"
        assert find_term_variant_issues(text, ["腐蚀速率"]) == []


class TestRegexSafetyAndLimits:

    def test_regex_metacharacters_in_term_do_not_raise(self):
        """标准号含斜杠、连字符与括号，必须转义后匹配。"""
        term = "硫化氢环境人身防护规范（SY/T 6277-2017）"
        text = f"执行{term}，并按 硫化氢环境人身防护规范(SY/T 6277-2017) 培训。"
        issues = find_term_variant_issues(text, [term])
        assert len(issues) == 1

    def test_issue_count_capped(self):
        terms = [f"TERM{index}体系" for index in range(1, 12)]
        text = " ".join(f"{term} 与 {term.lower()}" for term in terms)
        issues = find_term_variant_issues(text, terms)
        assert len(issues) == 5

    def test_custom_cap_respected(self):
        terms = [f"TERM{index}体系" for index in range(1, 6)]
        text = " ".join(f"{term} 与 {term.lower()}" for term in terms)
        assert len(find_term_variant_issues(text, terms, max_issues=2)) == 2

    def test_one_issue_per_term_at_most(self):
        """同一术语同时存在两类变体时只报一条，避免重复占位。"""
        term = "光谱仪（ICP）"
        text = f"{term} 与 光谱仪(icp) 并存"
        assert len(find_term_variant_issues(text, [term])) == 1

    @pytest.mark.parametrize(
        "text,terms",
        [
            ("", ["HSE体系"]),
            ("正文内容", []),
            ("正文内容", ["A"]),
        ],
    )
    def test_empty_inputs_return_no_issues(self, text, terms):
        assert find_term_variant_issues(text, terms) == []

    def test_non_positive_cap_returns_no_issues(self):
        text = "HSE体系 与 hse体系"
        assert find_term_variant_issues(text, ["HSE体系"], max_issues=0) == []


class TestCheckerIntegration:

    def test_checker_merges_profile_term_issues(self):
        from types import SimpleNamespace

        from app.core.writer.consistency_checker import ConsistencyChecker

        checker = ConsistencyChecker.__new__(ConsistencyChecker)
        drafts = {
            1: SimpleNamespace(
                content="投标人执行 HSE管理体系，同时按 hse管理体系 检查。"
            )
        }
        issues = checker._check_terminology(
            drafts, profile_terms=["HSE管理体系"]
        )
        assert any("HSE管理体系" in issue["message"] for issue in issues)

    def test_checker_without_profile_terms_keeps_legacy_behavior(self):
        from types import SimpleNamespace

        from app.core.writer.consistency_checker import ConsistencyChecker

        checker = ConsistencyChecker.__new__(ConsistencyChecker)
        drafts = {1: SimpleNamespace(content="甲方与招标人并列出现。")}
        issues = checker._check_terminology(drafts)
        assert len(issues) == 1
        assert "招标方称谓" in issues[0]["message"]

    def test_profile_content_located_by_sub_type(self):
        from types import SimpleNamespace

        from app.core.writer.consistency_checker import ConsistencyChecker

        clauses = [
            SimpleNamespace(sub_type="scoring_criterion", content="评分项"),
            SimpleNamespace(sub_type="industry_profile", content="### 2. 专业术语表\n1. 多相流"),
        ]
        assert (
            ConsistencyChecker._find_industry_profile_content(clauses)
            == "### 2. 专业术语表\n1. 多相流"
        )

    def test_missing_profile_clause_returns_none(self):
        from types import SimpleNamespace

        from app.core.writer.consistency_checker import ConsistencyChecker

        clauses = [SimpleNamespace(sub_type="scoring_criterion", content="评分项")]
        assert ConsistencyChecker._find_industry_profile_content(clauses) is None
