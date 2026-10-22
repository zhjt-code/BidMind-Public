"""sanitize_quote 边界值测试

验证 LLM 语义空值标准化逻辑，防止 "无"/"null" 等文本被当作有效引文。
"""

import pytest

from app.schemas.collusion import sanitize_quote


@pytest.mark.parametrize("input_val,expected", [
    (None, None),
    ("", None),
    ("  ", None),
    ("无", None),
    (" 无 ", None),
    ("null", None),
    ("Null", None),
    ("NULL", None),
    ("none", None),
    ("None", None),
    ("NONE", None),
    ("n/a", None),
    ("N/A", None),
    ("NA", None),
    ("na", None),
    ("不适用", None),
    ("未找到", None),
    ("无相关内容", None),
    ("未提取到", None),
    ("/", None),
    ("-", None),
    ("--", None),
    ("——", None),
])
def test_semantic_nulls_are_sanitized(input_val: str | None, expected: None) -> None:
    assert sanitize_quote(input_val) is expected


@pytest.mark.parametrize("input_val", [
    "姓名 <PERSON_5> ... 身份证号 XXXX15",
    "第90页《施工资质》章节",
    '法定代表人：<PERSON_A>',
    "ISCO 260D高压注入泵",
    "报价: 1,234,567.89元",
    "无法确定该人员是否为同一自然人",
])
def test_valid_quotes_are_preserved(input_val: str) -> None:
    assert sanitize_quote(input_val) == input_val


def test_whitespace_only_strings() -> None:
    assert sanitize_quote("\t\n") is None
    assert sanitize_quote("   \t  ") is None


def test_case_insensitive_matching() -> None:
    assert sanitize_quote("nUlL") is None
    assert sanitize_quote("n/A") is None
