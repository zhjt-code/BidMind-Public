"""证据充分性评估防御测试（换轴重构 P1 A组,）。

职责: 验证 assess_item_evidence_sufficiency 的正常/边界/异常路径——空白名拒收、
 单字符归一名无规则文本拒收、非空规则豁免、逐条目异常转标记不抛出。
不做: 不测捕获/对账逻辑、不读语料、不触发 LLM/DB。
"""
from __future__ import annotations

import pytest

from app.core.extractor.scoring.ledger.evidence_sufficiency import assess_item_evidence_sufficiency

pytestmark = pytest.mark.scoring


def test_all_sufficient_entries_pass():
    """正常路径: 名称与规则文本齐备的条目全部通过, 无拒收无标记。"""
    entries = [("投标报价", 50.0, "按偏差率计分"), ("项目业绩", 20.0, "每项业绩得2分")]
    insufficient, flags = assess_item_evidence_sufficiency(entries)
    assert insufficient == ()
    assert flags == ()


def test_blank_name_rejected():
    """: 名称为空串的条目拒收并输出 fail-visible 标记。"""
    insufficient, flags = assess_item_evidence_sufficiency([("", 10.0, "有规则文本")])
    assert insufficient == (0,)
    assert len(flags) == 1 and "" in flags[0]


def test_whitespace_only_name_rejected():
    """: 名称仅空白(含全角空格)视同空白拒收, 规则文本非空也不豁免。"""
    insufficient, _flags = assess_item_evidence_sufficiency([("  \u3000 ", 5.0, "规则")])
    assert insufficient == (0,)


def test_single_char_name_without_rule_rejected():
    """: 单字符名且规则文本仅空白 → 不充分证据拒收。"""
    insufficient, flags = assess_item_evidence_sufficiency([("优", 5.0, "   ")])
    assert insufficient == (0,)
    assert "entry[0]" in flags[0]


def test_single_char_name_with_rule_kept():
    """边界: 单字符名但规则文本非空 → 证据可用, 不拒收。"""
    insufficient, flags = assess_item_evidence_sufficiency([("优", 5.0, "全部满足得5分")])
    assert insufficient == ()
    assert flags == ()


def test_business_suffix_name_without_rule_kept():
    """: 项名不得按组标签规则剥业务尾缀——"评分"紧凑后仍为2字, 不拒收。

 行为变更依据: 该判据原复用组标签归一器 normalize_group_label(它剥离
 "项/分/标准/部分"等组语义尾缀), 在项名上把"扣分项"削成单字"扣"造成误拒
 (项目2449)。原防御内核"名称退化到单字符即无信息量"未被削弱, 由
 test_single_char_name_without_rule_rejected 与混合索引用例继续锁定。
 """
    insufficient, flags = assess_item_evidence_sufficiency([("评分", 5.0, "")])
    assert insufficient == ()
    assert flags == ()


def test_deduction_item_name_with_deduction_rule_text_kept():
    """ 真实形态(项目2449 req36974): 项名"扣分项"+规则文本来自扣分规则,
 必须不拒收。此前双重误判(项名削成"扣" + 规则只读 score_rules)使分值全闭合
 的文档在确认页仍报"部分评分项缺少评审标准"。"""
    insufficient, flags = assess_item_evidence_sufficiency(
        [("扣分项", 0.0, "未按要求响应的每项扣1分")]
    )
    assert insufficient == ()
    assert flags == ()


def test_deduction_item_name_without_any_rule_kept():
    """ 边界: 即便无任何规则文本, "扣分项"紧凑后3字仍有信息量, 不拒收。"""
    insufficient, _flags = assess_item_evidence_sufficiency([("扣分项", 0.0, "")])
    assert insufficient == ()


def test_fullwidth_single_char_name_without_rule_rejected():
    """边界: 全角单字符名(NFKC 归一后仍为1字)且无规则文本 → 保持拒收。"""
    insufficient, flags = assess_item_evidence_sufficiency([("Ａ", 5.0, "")])
    assert insufficient == (0,)
    assert "entry[0]" in flags[0]


def test_entry_exception_converted_to_flag():
    """异常路径: 条目处理抛异常时转为拒收标记而非抛出(调用边界防御)。"""

    class _Boom:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    entries = [("正常项", 10.0, "规则"), (_Boom(), 5.0, "规则")]
    insufficient, flags = assess_item_evidence_sufficiency(entries)  # type: ignore[arg-type]
    assert insufficient == (1,)
    assert len(flags) == 1 and "RuntimeError" in flags[0]


def test_mixed_entries_indices_are_positional():
    """混合路径: 拒收索引按原始位置返回, 合格条目不受影响。"""
    entries = [
        ("投标报价", 50.0, "按偏差率计分"),
        ("", 1.0, "规则"),
        ("分", 1.0, ""),
        ("技术方案", 30.0, "施工组织设计完整得30分"),
    ]
    insufficient, flags = assess_item_evidence_sufficiency(entries)
    assert insufficient == (1, 2)
    assert len(flags) == 2
