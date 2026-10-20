"""P1.1 防御测试：交叉引用扩展正则覆盖多种表达。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.consistency_checker import _CROSS_REF_PATTERN
from app.core.ai.gates.writer.cross_reference import _REF_PATTERN


def _any_group_non_empty(match) -> bool:
    if isinstance(match, str):
        return bool(match.strip())
    return any(bool(g) for g in match)


@pytest.mark.parametrize("text", [
    "详见第三章",
    "见附录B",       # 附录 + 标识符分支
    "参见第五部分",
    "参照技术方案章",
    "参考管理制度附件",
    "如前所述本章节",
])
def test_checker_cross_ref_pattern_matches(text: str):
    """consistency_checker 的扩展正则覆盖 见/详见/参见/参照/参考/如前所述/附录。"""
    matches = _CROSS_REF_PATTERN.findall(text)
    matched_non_empty = [m for m in matches if _any_group_non_empty(m)]
    assert matched_non_empty, f"{text!r} 未匹配 (raw={matches!r})"


@pytest.mark.parametrize("text", [
    "详见第三章",
    "参见第五节",
    "参照投标函部分",
    "详见附录A",
])
def test_gate_cross_ref_pattern_matches(text: str):
    """Gate 侧正则与 consistency_checker 保持一致扩展。"""
    matches = _REF_PATTERN.findall(text)
    matched_non_empty = [m for m in matches if _any_group_non_empty(m)]
    assert matched_non_empty, f"gate 正则未匹配 {text!r}"


def test_legacy_single_expression_still_matched():
    """旧正则只匹配'详见XX章'也能继续被新正则覆盖。"""
    text = "详见第一章"
    matches = _CROSS_REF_PATTERN.findall(text)
    assert any(_any_group_non_empty(m) for m in matches)


def test_unrelated_text_not_matched():
    """无引用表达的正文不应误匹配。"""
    text = "本公司致力于提供高质量的服务,满足客户需求"
    matches = _CROSS_REF_PATTERN.findall(text)
    non_empty = [m for m in matches if _any_group_non_empty(m)]
    assert non_empty == []
