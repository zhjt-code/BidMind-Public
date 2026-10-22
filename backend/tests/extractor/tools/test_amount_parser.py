"""Tests for amount_parser.py — 中文货币金额确定性解析器

测试分层
--------
L0 — 纯阿拉伯数字                  (5 cases)
L1 — 纯中文数字                    (4 cases)
L2 — 混合（阿拉伯系数 + 中文单位）  (4 cases)
L3 — 全角输入                      (2 cases)
L4 — 区间表达式                    (2 cases)
L5 — 单位边界 & 无单位              (4 cases)
L6 — compare_amount 逻辑           (6 cases)
L7 — 降级（无法解析场景）           (3 cases)
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.extractor.tools.amount_parser import (
    AmountVerdict,
    compare_amount,
    parse_amount,
)

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _val(text: str) -> Decimal | None:
    return parse_amount(text).canonical_value


def _unit(text: str) -> str | None:
    return parse_amount(text).unit


def _conf(text: str) -> str:
    return parse_amount(text).confidence


# ---------------------------------------------------------------------------
# L0 纯阿拉伯数字  (5 cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_yuan", "expected_unit"),
    [
        ("500万元", Decimal("5000000"), "wan_yuan"),
        ("1200.50万元", Decimal("12005000.00"), "wan_yuan"),
        ("3,500,000元", Decimal("3500000"), "yuan"),
        ("5000元", Decimal("5000"), "yuan"),
        ("1000000元", Decimal("1000000"), "yuan"),
    ],
)
def test_pure_arabic(
    text: str, expected_yuan: Decimal, expected_unit: str
) -> None:
    """L0: 阿拉伯数字金额应精确解析，置信度 high。"""
    v = parse_amount(text)
    assert v.canonical_value == expected_yuan, f"canonical_value mismatch for {text!r}"
    assert v.unit == expected_unit, f"unit mismatch for {text!r}"
    assert v.confidence == "high"


# ---------------------------------------------------------------------------
# L1 纯中文数字  (4 cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_yuan", "expected_unit"),
    [
        ("伍佰万元", Decimal("5000000"), "wan_yuan"),
        ("叁佰伍拾万元整", Decimal("3500000"), "wan_yuan"),
        ("壹仟贰佰万元", Decimal("12000000"), "wan_yuan"),
        ("叁亿元", Decimal("300000000"), "yi_yuan"),
    ],
)
def test_pure_chinese(
    text: str, expected_yuan: Decimal, expected_unit: str
) -> None:
    """L1: 纯中文大写金额，含末尾"整"，应正确还原元值，置信度 medium。"""
    v = parse_amount(text)
    assert v.canonical_value == expected_yuan, f"canonical_value mismatch for {text!r}"
    assert v.unit == expected_unit, f"unit mismatch for {text!r}"
    assert v.confidence == "medium"


# ---------------------------------------------------------------------------
# L2 混合格式（阿拉伯系数 + 中文单位）  (4 cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_yuan", "expected_unit"),
    [
        ("500万", Decimal("5000000"), "wan_yuan"),
        ("3.5亿", Decimal("350000000.0"), "yi_yuan"),
        ("1200万元(含税)", Decimal("12000000"), "wan_yuan"),
        ("2.5亿元", Decimal("250000000.0"), "yi_yuan"),
    ],
)
def test_mixed_arabic_chinese_unit(
    text: str, expected_yuan: Decimal, expected_unit: str
) -> None:
    """L2: 混合格式——阿拉伯系数 + 中文量级单位，应高置信度解析。"""
    v = parse_amount(text)
    assert v.canonical_value == expected_yuan, f"canonical_value mismatch for {text!r}"
    assert v.unit == expected_unit
    assert v.confidence == "high"


# ---------------------------------------------------------------------------
# L3 全角输入  (2 cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_yuan"),
    [
        ("５００万元", Decimal("5000000")),
        ("１，２００万元", Decimal("12000000")),
    ],
)
def test_fullwidth(text: str, expected_yuan: Decimal) -> None:
    """L3: 全角数字及全角逗号应先规范化再解析。"""
    v = parse_amount(text)
    assert v.canonical_value == expected_yuan, f"canonical_value mismatch for {text!r}"
    assert v.confidence == "high"


# ---------------------------------------------------------------------------
# L4 区间表达式  (2 cases)
# ---------------------------------------------------------------------------


def test_range_hyphen() -> None:
    """L4a: 连字符区间取下界，置信度 medium。"""
    v = parse_amount("500-800万元")
    assert v.canonical_value == Decimal("5000000")
    assert v.unit == "wan_yuan"
    assert v.confidence == "medium"
    assert any("range" in t or "下界" in t for t in v.parse_trace)


def test_range_tilde() -> None:
    """L4b: 波浪线区间取下界，置信度 medium。"""
    v = parse_amount("300~500万")
    assert v.canonical_value == Decimal("3000000")
    assert v.unit == "wan_yuan"
    assert v.confidence == "medium"


# ---------------------------------------------------------------------------
# L5 单位边界 & 无单位  (4 cases)
# ---------------------------------------------------------------------------


def test_unit_qian_yuan() -> None:
    """L5a: "千元"单位（有系数）。"""
    v = parse_amount("叁千元")
    assert v.canonical_value == Decimal("3000")
    assert v.unit == "yuan"
    assert v.confidence == "medium"


def test_unit_baiwan_yuan() -> None:
    """L5b: "百万元"形式，等价于 1,000,000 yuan。"""
    v = parse_amount("百万元")
    assert v.canonical_value == Decimal("1000000")
    assert v.unit == "wan_yuan"


def test_unit_no_unit_arabic() -> None:
    """L5c: 纯阿拉伯数字无单位，无法确定元值 → canonical_value=None。"""
    v = parse_amount("500")
    assert v.canonical_value is None
    assert v.confidence == "low"


def test_unit_wan_compound_yi() -> None:
    """L5d: 含亿的复合中文数字（三亿五千万元），pycnnum 可正确处理。"""
    v = parse_amount("三亿五千万元")
    assert v.canonical_value == Decimal("350000000")
    assert v.confidence == "medium"


# ---------------------------------------------------------------------------
# L6 compare_amount 逻辑  (6 cases)
# ---------------------------------------------------------------------------


def test_compare_meets_exact() -> None:
    """L6a: 响应等于要求 → meets。"""
    assert compare_amount("500万元", "500万元") == "meets"


def test_compare_below() -> None:
    """L6b: 响应低于要求 → below。"""
    assert compare_amount("500万元", "400万元") == "below"


def test_compare_above() -> None:
    """L6c: 响应超出要求 → above。"""
    assert compare_amount("500万元", "600万元") == "above"


def test_compare_meets_within_tolerance() -> None:
    """L6d: 在容差范围内，响应略低于要求 → meets。"""
    # threshold = 500万 × 0.95 = 475万；响应 475万 正好在边界
    assert compare_amount("500万元", "475万元", tolerance_pct=5.0) == "meets"


def test_compare_below_outside_tolerance() -> None:
    """L6e: 超出容差范围 → below。"""
    # threshold = 500万 × 0.95 = 475万；响应 474万 < 475万
    assert compare_amount("500万元", "474万元", tolerance_pct=5.0) == "below"


def test_compare_unparseable() -> None:
    """L6f: 任一方无法解析 → unparseable。"""
    assert compare_amount("500万元", "无效文本xyz") == "unparseable"
    assert compare_amount("无效文本abc", "500万元") == "unparseable"


# ---------------------------------------------------------------------------
# L7 降级场景（NEVER canonical_value；不可解析时不猜测）  (3 cases)
# ---------------------------------------------------------------------------


def test_degrade_empty_string() -> None:
    """L7a: 空字符串不可解析。"""
    v = parse_amount("")
    assert v.canonical_value is None
    assert v.confidence == "low"


def test_degrade_pure_noise() -> None:
    """L7b: 纯噪声字符串不可解析。"""
    v = parse_amount("详见附件")
    assert v.canonical_value is None
    assert v.confidence == "low"


def test_degrade_partial_text() -> None:
    """L7c: 含金额关键字但无有效数字 → 不猜测，返回 None。"""
    v = parse_amount("不低于若干万元")
    assert v.canonical_value is None
    assert v.confidence == "low"


# ---------------------------------------------------------------------------
# 额外边界测试 — 确保 parse_trace 填充
# ---------------------------------------------------------------------------


def test_parse_trace_populated() -> None:
    """parse_trace 应记录解析过程，便于调试。"""
    v = parse_amount("500万元")
    assert len(v.parse_trace) > 0


def test_parenthetical_stripped() -> None:
    """括号注记（含税）应被剥离后再解析，不影响解析结果。"""
    with_paren = parse_amount("1200万元(含税)")
    without_paren = parse_amount("1200万元")
    assert with_paren.canonical_value == without_paren.canonical_value


def test_fullwidth_range() -> None:
    """全角连字符区间也应被正确识别。"""
    # ５００－８００万元（全角减号 FF0D）
    v = parse_amount("\uff15\uff10\uff10\uff0d\uff18\uff10\uff10\u4e07\u5143")
    assert v.canonical_value == Decimal("5000000")
    assert v.confidence == "medium"


def test_amount_verdict_is_frozen() -> None:
    """AmountVerdict 为 frozen dataclass，属性不可重新赋值。"""
    v = AmountVerdict(canonical_value=Decimal("100"))
    with pytest.raises((AttributeError, TypeError)):
        v.canonical_value = Decimal("200")  # type: ignore[misc]


def test_chinese_yi_decimal() -> None:
    """中文小数亿：三点五亿 → 350,000,000。"""
    v = parse_amount("三点五亿元")
    assert v.canonical_value == Decimal("350000000.0")
    assert v.unit == "yi_yuan"
    assert v.confidence == "medium"
