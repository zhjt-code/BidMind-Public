"""P1.1 防御测试：ConsistencyChecker 固定措辞 diff 检查。

验证:
- draft 内容与 raw_text 相似度 < 0.7 时告警 "fixed_template_modified";
- 相似度 >= 0.7 时不告警;
- [待补充] 占位符依然能触发 "fixed_template_incomplete" 告警 (保留旧能力);
- 未能匹配到 fixed_template clause 时不崩溃，并告警模板来源缺失风险。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.consistency_checker import ConsistencyChecker
from app.core.writer.fixed_template_validator import validate_fixed_template
from app.models.writing_outline_node import OutlineNodeType


@dataclass
class _FakeDraft:
    content: str


@dataclass
class _FakeNode:
    id: int
    title: str
    node_type: OutlineNodeType = OutlineNodeType.fixed_template


@dataclass
class _FakeClause:
    sub_type: str
    content: str
    raw_text: str | None = None


class TestFixedWordingDiff:

    def _make_checker(self):
        checker = ConsistencyChecker.__new__(ConsistencyChecker)
        checker.db = None  # 不会用到, 以下方法是纯算法
        checker.llm_client = None
        return checker

    def test_high_similarity_no_issue(self):
        checker = self._make_checker()
        raw = "投标函本公司承诺严格遵守招标文件要求, 按期保质完成项目"
        # draft 与 raw 完全一致
        drafts = {1: _FakeDraft(content=raw)}
        nodes = [_FakeNode(id=1, title="投标函")]
        clauses = [_FakeClause(
            sub_type="fixed_template", content="投标函模板", raw_text=raw,
        )]
        issues = checker._check_fixed_wording(drafts, nodes, clauses)
        # 只可能报 incomplete (此处 draft 无 [待补充], 所以为空)
        assert all(i["type"] != "fixed_template_modified" for i in issues)

    def test_low_similarity_triggers_modified(self):
        checker = self._make_checker()
        raw = "投标函本公司承诺严格遵守招标文件要求, 按期保质完成项目"
        draft_text = "完全不一样的内容, 这里是胡乱写的段落, 与原始模板毫无关联"
        drafts = {1: _FakeDraft(content=draft_text)}
        nodes = [_FakeNode(id=1, title="投标函")]
        clauses = [_FakeClause(
            sub_type="fixed_template", content="投标函模板", raw_text=raw,
        )]
        issues = checker._check_fixed_wording(drafts, nodes, clauses)
        modified = [i for i in issues if i["type"] == "fixed_template_modified"]
        assert len(modified) == 1
        assert modified[0]["severity"] == "high"

    def test_incomplete_placeholder_still_detected(self):
        checker = self._make_checker()
        raw = "投标函本公司承诺严格遵守招标文件要求"
        # draft 与 raw 高度相似, 但含 [待补充]
        drafts = {1: _FakeDraft(content=raw + " [待补充:公司名称]")}
        nodes = [_FakeNode(id=1, title="投标函")]
        clauses = [_FakeClause(
            sub_type="fixed_template", content="投标函模板", raw_text=raw,
        )]
        issues = checker._check_fixed_wording(drafts, nodes, clauses)
        types = {i["type"] for i in issues}
        assert "fixed_template_incomplete" in types

    def test_no_matching_clause_no_modified_issue(self):
        checker = self._make_checker()
        drafts = {1: _FakeDraft(content="一段正文")}
        nodes = [_FakeNode(id=1, title="开标一览表")]
        clauses = [_FakeClause(
            sub_type="fixed_template", content="投标函模板",
            raw_text="投标函原文...", 
        )]
        issues = checker._check_fixed_wording(drafts, nodes, clauses)
        assert all(i["type"] != "fixed_template_modified" for i in issues)
        missing = [i for i in issues if i["type"] == "fixed_template_source_missing"]
        assert len(missing) == 1
        assert missing[0]["severity"] == "high"

    def test_non_fixed_template_node_ignored(self):
        checker = self._make_checker()
        drafts = {1: _FakeDraft(content="任何内容")}
        nodes = [_FakeNode(id=1, title="技术方案", node_type=OutlineNodeType.free_writing)]
        clauses = [_FakeClause(
            sub_type="fixed_template", content="x", raw_text="y",
        )]
        issues = checker._check_fixed_wording(drafts, nodes, clauses)
        assert issues == []


def test_fixed_template_validator_detects_missing_required_fields():
    result = validate_fixed_template(
        node_id=1,
        node_title="投标函",
        source_text="投标人：____ 法定代表人：____ 日期：____ 盖章：____",
        draft_content="投标人：示例公司 日期：2026年5月19日",
    )

    types = {issue["type"] for issue in result.issues}
    assert "fixed_template_field_missing" in types
    assert "fixed_template_signature_slot_missing" in types


def test_fixed_template_validator_detects_table_mismatch():
    source = """
    <table>
      <tr><th>序号</th><th>项目名称</th><th>报价</th></tr>
      <tr><td>1</td><td>[待补充:项目名称]</td><td>[待补充:报价]</td></tr>
    </table>
    """
    draft = """
    <table>
      <tr><th>序号</th><th>项目名称</th></tr>
      <tr><td>1</td><td>项目A</td></tr>
    </table>
    """

    result = validate_fixed_template(
        node_id=2,
        node_title="开标一览表",
        source_text=source,
        draft_content=draft,
    )

    assert any(issue["type"] == "fixed_template_table_mismatch" for issue in result.issues)
