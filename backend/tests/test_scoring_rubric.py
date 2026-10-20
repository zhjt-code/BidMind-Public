"""scoring_rubric.py 防御测试 (Wave2-S5 P1 B1+B2+B3)

映射真实风险:
- rule_assessments 归一化: 非法 status 降级 unknown, points clamp
- estimated_score 与 rule_assessments points 之和不一致时的保守处理
- 空输入兼容
- build_rule_assessments_prompt_block 输出格式
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.ai.reviewer_parts.scoring_rubric import (
    normalize_rule_assessments,
    reconcile_estimated_score,
    build_rule_assessments_prompt_block,
)

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# normalize_rule_assessments
# ---------------------------------------------------------------------------


def test_normalize_empty_returns_none():
    assert normalize_rule_assessments(None) is None
    assert normalize_rule_assessments([]) is None


def test_normalize_valid_items():
    items = [
        {
            "rule_excerpt": "配置企业导师1名",
            "status": "met",
            "points": 5.0,
            "reason": "投标文件第15页已配备",
            "evidence_pages": [15],
        },
        {
            "rule_excerpt": "配置兼职讲师2名",
            "status": "partial",
            "points": 3.0,
            "reason": "仅配备1名",
            "evidence_pages": [16, 17],
        },
    ]
    result = normalize_rule_assessments(items, max_score=10.0)
    assert result is not None
    assert len(result) == 2
    assert result[0]["status"] == "met"
    assert result[0]["points"] == 5.0
    assert result[1]["status"] == "partial"


def test_normalize_invalid_status_degraded_to_unknown():
    """非法 status 字段必须降级为 unknown。"""
    items = [
        {
            "rule_excerpt": "某条规则",
            "status": "INVALID_STATUS",
            "points": 2.0,
            "reason": "无法判定",
            "evidence_pages": [],
        }
    ]
    result = normalize_rule_assessments(items)
    assert result is not None
    assert result[0]["status"] == "unknown"


def test_normalize_points_clamped_to_max_score():
    """points 超过 max_score 时应 clamp。"""
    items = [
        {
            "rule_excerpt": "某规则",
            "status": "met",
            "points": 999.0,
            "reason": "超过满分",
            "evidence_pages": [],
        }
    ]
    result = normalize_rule_assessments(items, max_score=10.0)
    assert result is not None
    assert result[0]["points"] == 10.0


def test_normalize_points_null_allowed():
    """points 为 null 时合法，不应报错。"""
    items = [
        {
            "rule_excerpt": "不可量化规则",
            "status": "met",
            "points": None,
            "reason": "满足但无具体分值",
            "evidence_pages": [],
        }
    ]
    result = normalize_rule_assessments(items)
    assert result is not None
    assert result[0]["points"] is None


def test_normalize_all_invalid_items_returns_none():
    """所有条目都非法时返回 None 而非空列表。"""
    # 传入完全无效的数据（非 dict / 非 model）
    result = normalize_rule_assessments(["not_a_dict", 42, None])
    assert result is None


def test_normalize_skips_non_dict_items():
    """混合有效/无效条目时, 跳过无效条目, 仍返回有效部分。"""
    items = [
        "invalid_string",
        {
            "rule_excerpt": "有效规则",
            "status": "not",
            "points": 0.0,
            "reason": "未提供",
            "evidence_pages": [],
        },
    ]
    result = normalize_rule_assessments(items)
    assert result is not None
    assert len(result) == 1
    assert result[0]["status"] == "not"


# ---------------------------------------------------------------------------
# reconcile_estimated_score
# ---------------------------------------------------------------------------


def test_reconcile_consistent_scores():
    """estimated_score 与 points 之和一致(误差<=0.5)时直接返回。"""
    assessments = [
        {"rule_excerpt": "规则A", "status": "met", "points": 5.0, "reason": "", "evidence_pages": []},
        {"rule_excerpt": "规则B", "status": "partial", "points": 3.0, "reason": "", "evidence_pages": []},
    ]
    score, note = reconcile_estimated_score(8.0, assessments, max_score=10.0)
    assert score == 8.0
    assert note is None


def test_reconcile_inconsistent_scores_keeps_estimated():
    """不一致时取较低分；本例 estimated 更低，仍为 5。"""
    assessments = [
        {"rule_excerpt": "规则A", "status": "met", "points": 9.0, "reason": "", "evidence_pages": []},
    ]
    score, note = reconcile_estimated_score(5.0, assessments, max_score=10.0)
    assert score == 5.0
    assert note is not None
    assert "不一致" in note


def test_reconcile_inconsistent_takes_lower_when_estimated_is_max():
    """LLM 预估满分但规则分更低时，必须取规则分，禁止保留满分预估。"""
    assessments = [
        {"rule_excerpt": "规则A", "status": "partial", "points": 6.0, "reason": "", "evidence_pages": []},
    ]
    score, note = reconcile_estimated_score(10.0, assessments, max_score=10.0)
    assert score == 6.0
    assert note is not None
    assert "取较低" in note


def test_reconcile_none_assessments():
    """assessments 为 None 时直接返回 estimated_score, 无 note。"""
    score, note = reconcile_estimated_score(7.0, None, max_score=10.0)
    assert score == 7.0
    assert note is None


def test_reconcile_none_estimated_score():
    """estimated_score 为 None 时直接返回。"""
    assessments = [
        {"rule_excerpt": "规则A", "status": "met", "points": 5.0, "reason": "", "evidence_pages": []},
    ]
    score, note = reconcile_estimated_score(None, assessments, max_score=10.0)
    assert score is None
    assert note is None


def test_reconcile_items_with_null_points_excluded():
    """points=null 的条目不计入求和。"""
    assessments = [
        {"rule_excerpt": "可量化规则", "status": "met", "points": 6.0, "reason": "", "evidence_pages": []},
        {"rule_excerpt": "不可量化规则", "status": "met", "points": None, "reason": "", "evidence_pages": []},
    ]
    score, note = reconcile_estimated_score(6.0, assessments, max_score=10.0)
    assert score == 6.0
    assert note is None


# ---------------------------------------------------------------------------
# build_rule_assessments_prompt_block
# ---------------------------------------------------------------------------


def test_prompt_block_empty_when_no_rules():
    """无评分规则时不输出补充块。"""
    block = build_rule_assessments_prompt_block({})
    assert block == ""


def test_prompt_block_contains_rule_assessments_keyword():
    """有评分规则时 prompt 块包含 rule_assessments 关键词。"""
    meta = {
        "score_rules": ["配备导师1名得5分", "配备助教1名得3分"],
        "deduction_rules": [],
    }
    block = build_rule_assessments_prompt_block(meta)
    assert "rule_assessments" in block
    assert "met" in block
    assert "partial" in block


def test_prompt_block_with_deduction_only():
    """仅有扣分规则时也应输出 prompt 块。"""
    meta = {"score_rules": [], "deduction_rules": ["未提供证明扣2分"]}
    block = build_rule_assessments_prompt_block(meta)
    assert "rule_assessments" in block
