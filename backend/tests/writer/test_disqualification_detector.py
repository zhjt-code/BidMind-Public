"""P1.1 防御测试：disqualification_detector 结构化废标检测。

对照旧伪算法（"关键词 + 否定词"）, 验证新实现:
- 能把"禁止转包 + 投标方承诺不转包"判为合规 (不告警);
- 能把"禁止分包 + 投标方声明可分包"判为违反 (告警);
- 无禁止动作时返回空;
- 非 disqualification sub_type 被忽略。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.disqualification_detector import (
    _extract_forbidden_actions,
    find_disqualification_issues,
)


@dataclass
class _FakeClause:
    sub_type: str
    content: str


class TestExtractForbiddenActions:

    def test_extracts_action_after_neg(self):
        acts = _extract_forbidden_actions("投标人不得转包或分包本项目")
        assert "转包或分包本项" in acts or "转包或分包" in " ".join(acts)

    def test_multiple_negations(self):
        text = "不得转包,禁止围标,严禁串标"
        acts = _extract_forbidden_actions(text)
        assert len(acts) >= 2

    def test_empty_text(self):
        assert _extract_forbidden_actions("") == []

    def test_no_negation(self):
        assert _extract_forbidden_actions("本公司严格遵守招标要求") == []


class TestFindDisqualificationIssues:

    def test_compliance_commitment_does_not_trigger(self):
        """投标方明确承诺不做该动作时不告警。"""
        clauses = [_FakeClause("disqualification", "不得转包本项目")]
        full_text = "本公司承诺严格遵守招标要求,不转包本项目,全部自行完成"
        issues = find_disqualification_issues(full_text, clauses)
        assert issues == []

    def test_positive_commitment_triggers_warning(self):
        """投标方声明可做被禁止动作时告警。"""
        clauses = [_FakeClause("disqualification", "不得分包本项目的主体工程")]
        full_text = "本公司将分包本项目的部分主体工程给合作方"
        issues = find_disqualification_issues(full_text, clauses)
        assert len(issues) == 1
        assert issues[0]["severity"] == "high"
        assert "分包" in issues[0]["message"]

    def test_non_disqualification_subtype_ignored(self):
        """structure_constraint 等其他 sub_type 的 clause 被忽略。"""
        clauses = [_FakeClause("structure_constraint", "不得分包")]
        full_text = "本公司将分包部分任务"
        issues = find_disqualification_issues(full_text, clauses)
        assert issues == []

    def test_no_negation_in_clause_skipped(self):
        """约束里没有'不得/禁止'等词时也不告警 (无法抽取动作)。"""
        clauses = [_FakeClause("disqualification", "投标人应当独立完成")]
        full_text = "本公司将独立完成本项目"
        issues = find_disqualification_issues(full_text, clauses)
        assert issues == []

    def test_empty_full_text(self):
        clauses = [_FakeClause("disqualification", "不得转包")]
        assert find_disqualification_issues("", clauses) == []

    def test_single_clause_reports_once(self):
        """同一 disq_clause 命中多动作只报一次, 避免重复刷屏。"""
        clauses = [_FakeClause(
            "disqualification", "不得转包、不得分包、不得串标"
        )]
        full_text = "本公司将转包并分包部分内容, 且可能存在串标行为"
        issues = find_disqualification_issues(full_text, clauses)
        assert len(issues) == 1

    def test_legacy_false_positive_fixed(self):
        """旧伪算法的典型 false positive 情景: 约束有'不得 + 关键词',
        投标方以'承诺不做'表述, 旧算法会误报, 新算法应放行。"""
        clauses = [_FakeClause(
            "disqualification", "不得存在财务严重不良状况"
        )]
        full_text = (
            "本公司财务状况良好, 不存在严重不良状况, 最近三年无重大违规"
        )
        issues = find_disqualification_issues(full_text, clauses)
        assert issues == []
