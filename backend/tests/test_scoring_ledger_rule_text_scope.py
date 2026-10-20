"""提取侧账本充分性判定的规则文本来源范围防御测试。

职责: 锁定 extractor_bridge 喂给充分性判定的规则文本同时覆盖 score_rules 与
 deduction_rules, 且原防御内核(单字符项名且确无任何规则文本)未被放开。
不做: 不测确认页侧视图(见 test_scoring_ledger_report_service)、不测捕获/对账
 数值逻辑(见 test_scoring_ledger_reconciliation)、不触发 DB/LLM。
"""
from __future__ import annotations

from app.core.extractor.scoring.ledger.extractor_bridge import (
    observe_scoring_ledger_shadow,
)

import pytest

pytestmark = pytest.mark.scoring


class _FakeChunk:
    """scoring_table chunk 最小形态（无 grid 载荷, 捕获落 L4 不影响充分性断言）。"""

    def __init__(self, page_num: int, content: str) -> None:
        self.page_num = page_num
        self.content = content


class _FakeItem:
    """提取侧条目最小形态（鸭子类型对齐 ExtractedScoringItem 读取协议）。"""

    def __init__(
        self,
        name: str,
        score: float,
        label: str,
        *,
        score_rules: list[str] | None = None,
        deduction_rules: list[str] | None = None,
    ) -> None:
        self.score_item_name = name
        self.score_weight = score
        self.volume_label = label
        self.score_rules = list(score_rules or [])
        self.deduction_rules = list(deduction_rules or [])


def _chunks() -> list[_FakeChunk]:
    return [_FakeChunk(page_num=1, content="第五章 评标办法")]


def test_only_deduction_rules_is_not_insufficient():
    """ 根因②: 单字符项名仅有扣分规则时, 漏读 deduction_rules 会被误判
 "无规则文本"而拒收; 合并两类规则后必须判为证据充分。"""
    items = [_FakeItem("优", 5.0, "商务技术评审", deduction_rules=["每项扣1分"])]

    report = observe_scoring_ledger_shadow(_chunks(), items, document_id=1)

    assert report is not None
    assert report.insufficiency_flags == ()


def test_only_score_rules_is_not_insufficient():
    """回归: 仅有评分规则的既有形态不受合并改动影响。"""
    items = [_FakeItem("优", 5.0, "商务技术评审", score_rules=["全部满足得5分"])]

    report = observe_scoring_ledger_shadow(_chunks(), items, document_id=1)

    assert report is not None
    assert report.insufficiency_flags == ()


def test_single_char_name_without_any_rule_still_insufficient():
    """防御内核未放开: 单字符项名且两类规则均为空 → 仍拒收并 fail-visible。"""
    items = [_FakeItem("优", 5.0, "商务技术评审")]

    report = observe_scoring_ledger_shadow(_chunks(), items, document_id=1)

    assert report is not None
    assert len(report.insufficiency_flags) == 1
    assert "entry[0]" in report.insufficiency_flags[0]


def test_deduction_item_name_survives_without_rules():
    """ 根因①(项目2449 req36974): "扣分项"不得被当作单字符项名拒收。"""
    items = [_FakeItem("扣分项", 0.0, "价格评审")]

    report = observe_scoring_ledger_shadow(_chunks(), items, document_id=1)

    assert report is not None
    assert report.insufficiency_flags == ()


def test_blank_rule_entries_do_not_count_as_rule_text():
    """边界: 规则列表内全为空白字符串时视同无规则, 不得靠占位项蒙混过关。"""
    items = [_FakeItem("优", 5.0, "商务技术评审", deduction_rules=["  ", ""])]

    report = observe_scoring_ledger_shadow(_chunks(), items, document_id=1)

    assert report is not None
    assert len(report.insufficiency_flags) == 1
