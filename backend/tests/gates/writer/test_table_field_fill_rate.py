"""S.5.2 单元测试：TableFieldFillRateGate。"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.writer.table_field_fill_rate import (
    TableFieldFillRateGate,
    _compute_fill_rate,
    _extract_td_texts,
)


def _ctx(content: str, node_type: str = "data_fill") -> GateContext:
    target = SimpleNamespace(content=content)
    return GateContext(
        flow="writer", stage="post_llm",
        target=target, evidence={"node_type": node_type}, config={},
    )


# ==================== 纯函数层 ====================

class TestExtractTdTexts:
    def test_basic_table(self):
        html = '<table><tr><td><PERSON_A></td><td>工程师</td></tr></table>'
        assert _extract_td_texts(html) == ['<PERSON_A>', "工程师"]

    def test_empty_td(self):
        html = '<tr><td><PERSON_A></td><td></td></tr>'
        assert _extract_td_texts(html) == ['<PERSON_A>', ""]

    def test_no_table_returns_empty(self):
        assert _extract_td_texts("纯段落文字，没有表格。") == []

    def test_nested_tags_stripped(self):
        html = '<td><b><PERSON_A></b></td>'
        # 实际 regex 会按最外层 td 抽，再去除内部 <b>
        assert _extract_td_texts(html) == ['<PERSON_A>']


class TestComputeFillRate:
    def test_all_filled(self):
        html = '<tr><td><PERSON_A></td><td>工程师</td></tr>'
        rate, non_empty, total = _compute_fill_rate(html)
        assert rate == 1.0 and non_empty == 2 and total == 2

    def test_half_empty(self):
        # 4 td, 序号 1/2 被过滤（纯数字），<PERSON_1>(非空)、""(空)
        html = (
            '<tr><td>1</td><td><PERSON_A></td><td></td></tr>'
            + '<tr><td>2</td><td><PERSON_B></td><td>工程师</td></tr>'
        )
        rate, non_empty, total = _compute_fill_rate(html)
        # 过滤掉 2 个纯数字序号，剩 4 个有语义 td：<PERSON_1>/""/<PERSON_2>/工程师 → 3/4
        assert total == 4
        assert non_empty == 3
        assert rate == 0.75

    def test_no_table_returns_perfect(self):
        rate, ne, t = _compute_fill_rate("纯段落")
        assert rate == 1.0 and ne == 0 and t == 0

    def test_all_empty(self):
        html = "<tr><td></td><td></td></tr>"
        rate, ne, total = _compute_fill_rate(html)
        assert rate == 0.0 and ne == 0 and total == 2


# ==================== Gate applies / evaluate ====================

class TestTableFieldFillRateGate:
    gate = TableFieldFillRateGate()

    def test_applies_only_to_data_fill(self):
        assert self.gate.applies(_ctx("<td>x</td>", node_type="data_fill")) is True
        assert self.gate.applies(_ctx("<td>x</td>", node_type="free_writing")) is False
        assert self.gate.applies(_ctx("<td>x</td>", node_type="fixed_template")) is False

    def test_applies_false_on_wrong_stage(self):
        target = SimpleNamespace(content="<td>x</td>")
        ctx = GateContext(
            flow="writer", stage="post_pipeline",
            target=target, evidence={"node_type": "data_fill"},
        )
        assert self.gate.applies(ctx) is False

    def test_pass_full_fill(self):
        html = '<tr><td><PERSON_A></td><td>工程师</td><td>10 年</td></tr>'
        v = self.gate.evaluate(_ctx(html))
        assert v.verdict == "pass"
        assert v.trace["fill_rate"] == 1.0

    def test_pass_no_table_content(self):
        v = self.gate.evaluate(_ctx("这是一段数据填充章节的描述文字"))
        assert v.verdict == "pass"
        assert v.trace["total_td"] == 0

    def test_warn_low_fill_rate(self):
        """4 个语义 td 只填 1 → 25%，低于默认 80% 阈值。"""
        html = (
            '<tr><td>1</td><td><PERSON_A></td><td></td><td></td></tr>'
            + "<tr><td>2</td><td></td><td></td><td></td></tr>"
        )
        v = self.gate.evaluate(_ctx(html))
        assert v.verdict == "warn"
        assert v.suggested_patch is not None
        assert v.suggested_patch["table_fill_rate_violated"] is True
        assert v.suggested_patch["failure_state"] == "needs_review"
        assert v.suggested_patch["table_fill_rate"] < 0.8

    def test_pass_at_exact_threshold(self):
        """填充率恰好 80% → pass。"""
        # 10 td，8 个非空（全过滤序号后是 10 个）
        cells = [f"<td>name{i}</td>" for i in range(8)] + ["<td></td>", "<td></td>"]
        html = "<tr>" + "".join(cells) + "</tr>"
        v = self.gate.evaluate(_ctx(html))
        assert v.verdict == "pass"
        assert v.trace["fill_rate"] == 0.8

    def test_config_override_threshold(self):
        """config 覆盖默认阈值。"""
        html = "<tr><td>x</td><td></td></tr>"  # 50%
        target = SimpleNamespace(content=html)
        ctx = GateContext(
            flow="writer", stage="post_llm",
            target=target, evidence={"node_type": "data_fill"},
            config={"table_fill_rate_threshold": 0.3},  # 降低阈值
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_severity_is_advisory(self):
        """advisory 起步（S.5.2 决策 D-033）。"""
        assert self.gate.default_severity == "advisory"
