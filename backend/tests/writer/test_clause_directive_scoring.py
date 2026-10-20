"""D1 防御测试: _format_scoring 在 score_rules 非空时补出评分标准补充。

风险映射: Wave1 S1 提取的评分项可能在 content 字段含有比 score_rules 更详细的条件描述
(如具体天数、测试步骤等), 当前代码仅在 score_rules/deduction_rules 均为空时才回退到
content 字段, 导致 content 中的差量信息丢失, 无法进入写作指令。

锁定契约:
- score_rules 非空 + content 含超出规则的差量文本 → 同时输出"得分要点"与"评分标准补充"。
- score_rules 非空 + content 基本重复了 score_rules 的核心词 → 不重复输出。
- content == name → 不输出补充 (name 已在标题中)。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.writer.composer.clause_directive import build_clause_directives

import pytest

pytestmark = pytest.mark.scoring


def _clause(sub_type, content, *, metadata=None, raw_text=None):
    return SimpleNamespace(
        sub_type=sub_type,
        content=content,
        raw_text=raw_text,
        metadata_json=metadata,
    )


# ==================== D1: 评分标准补充 ====================

def test_format_scoring_includes_supplement_when_content_has_extra_beyond_rules() -> None:
    """score_rules 非空但 content 含超出规则的详细条件 → 输出得分要点 + 评分标准补充。"""
    c = _clause("scoring_criterion",
                "工期保障：自合同生效之日起90天内完成相关测试并提交成果报告得6分",
                metadata={
                    "score_item_name": "工期保障",
                    "max_score": 6,
                    "score_rules": ["按时完成得6分"],
                })
    out = build_clause_directives([c], max_chars=2000)
    assert "得分要点" in out, "score_rules 应生成得分要点"
    assert "评分标准补充" in out, "content 有额外差量文本时应补出评分标准补充"
    assert "90天" in out, "content 中的具体条件应出现在输出中"


def test_format_scoring_no_supplement_when_score_rules_contained_in_content() -> None:
    """score_rules 文本已是 content 的子串 → 不重复输出评分标准补充 (避免冗余)。"""
    c = _clause("scoring_criterion", "方案覆盖全部技术指标得10分", metadata={
        "score_item_name": "技术方案完整性",
        "max_score": 10,
        "score_rules": ["覆盖全部技术指标得10分"],
    })
    out = build_clause_directives([c], max_chars=2000)
    assert "得分要点" in out
    assert "评分标准补充" not in out, "score_rules 已包含于 content 中, 不应重复输出"


def test_format_scoring_no_supplement_when_content_equals_name() -> None:
    """content 与 score_item_name 相同时, 不输出评分标准补充 (标题已体现)。"""
    c = _clause("scoring_criterion", "工期保障", metadata={
        "score_item_name": "工期保障",
        "max_score": 6,
        "score_rules": ["按时完成得6分"],
    })
    out = build_clause_directives([c], max_chars=2000)
    assert "得分要点" in out
    assert "评分标准补充" not in out, "content == name 时不应输出评分标准补充"


def test_format_scoring_existing_fallback_to_content_when_no_rules() -> None:
    """回归: score_rules 和 deduction_rules 均为空时, 仍回退到'评分标准：{content}'。"""
    c = _clause("scoring_criterion", "方案覆盖全部技术指标得10分，缺项扣2分", metadata={
        "score_item_name": "技术方案完整性",
        "max_score": 10,
        "score_rules": [],
        "deduction_rules": [],
    })
    out = build_clause_directives([c], max_chars=2000)
    assert "评分标准：方案覆盖全部技术指标得10分，缺项扣2分" in out
    assert "评分标准补充" not in out


def test_format_scoring_no_supplement_when_content_empty() -> None:
    """content 为空时, 不尝试输出补充。"""
    c = _clause("scoring_criterion", "", metadata={
        "score_item_name": "技术方案完整性",
        "max_score": 5,
        "score_rules": ["覆盖全部指标得满分"],
    })
    out = build_clause_directives([c], max_chars=2000)
    assert "评分标准补充" not in out
