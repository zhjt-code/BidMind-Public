"""Wave2-S5 P1 B1+B2+B3 schema/contract 防御测试 (RED 阶段)

验证:
1. ExtractedReviewResult 接受 rule_assessments 字段
2. ReviewResultResponse 包含 score_item_name/score_rules/deduction_rules/rule_assessments
3. ReviewResult 模型具有 rule_assessments 列
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 1. ExtractedReviewResult 接受 rule_assessments
# ---------------------------------------------------------------------------


def test_extracted_review_result_accepts_rule_assessments():
    """ExtractedReviewResult 应有 rule_assessments 可选字段, 默认 None。"""
    from app.schemas.review import ExtractedReviewResult

    result = ExtractedReviewResult(
        requirement_id=1,
        category="technical",
        requirement_summary="测试要求",
        response_summary="测试响应",
        is_compliant=True,
        compliance_level="fully",
        score=90,
        comment="符合要求" * 5,
        risk_level="none",
    )
    # 字段应存在且默认 None
    assert hasattr(result, "rule_assessments")
    assert result.rule_assessments is None


def test_extracted_review_result_with_rule_assessments():
    """ExtractedReviewResult 应可接受 rule_assessments 列表。"""
    from app.schemas.review import ExtractedReviewResult, RuleAssessmentItem

    assessments = [
        RuleAssessmentItem(
            rule_excerpt="配备导师1名",
            status="met",
            points=5.0,
            reason="投标文件第15页已配备",
            evidence_pages=[15],
        )
    ]
    result = ExtractedReviewResult(
        requirement_id=1,
        category="technical",
        requirement_summary="测试要求",
        response_summary="测试响应",
        is_compliant=True,
        compliance_level="fully",
        score=90,
        comment="符合要求" * 5,
        risk_level="none",
        rule_assessments=assessments,
    )
    assert result.rule_assessments is not None
    assert len(result.rule_assessments) == 1
    assert result.rule_assessments[0].status == "met"


def test_rule_assessment_item_invalid_status_normalized():
    """RuleAssessmentItem 非法 status 应降级为 unknown。"""
    from app.schemas.review import RuleAssessmentItem

    item = RuleAssessmentItem(
        rule_excerpt="某条规则",
        status="ILLEGAL",
        points=2.0,
        reason="测试",
        evidence_pages=[],
    )
    assert item.status == "unknown"


# ---------------------------------------------------------------------------
# 2. ReviewResultResponse 包含新字段
# ---------------------------------------------------------------------------


def test_review_result_response_has_new_fields():
    """ReviewResultResponse 应具有 score_item_name/score_rules/deduction_rules/rule_assessments。"""
    from app.schemas.review import ReviewResultResponse

    fields = ReviewResultResponse.model_fields
    assert "score_item_name" in fields, "缺少 score_item_name 字段"
    assert "score_rules" in fields, "缺少 score_rules 字段"
    assert "deduction_rules" in fields, "缺少 deduction_rules 字段"
    assert "rule_assessments" in fields, "缺少 rule_assessments 字段"


def test_review_result_response_new_fields_have_defaults():
    """新字段应有合理默认值以保证向后兼容。"""
    from app.schemas.review import ReviewResultResponse

    resp = ReviewResultResponse(
        id=1,
        project_id=1,
        requirement_id=1,
        category="technical",
        requirement_summary="测试",
        is_compliant=True,
        compliance_level="fully",
        score=90,
        comment="审查意见",
        risk_level="none",
    )
    # 默认应为 None 或空列表
    assert resp.score_item_name is None
    assert resp.score_rules == []
    assert resp.deduction_rules == []
    assert resp.rule_assessments is None


# ---------------------------------------------------------------------------
# 3. ReviewResult ORM 模型具有 rule_assessments 列
# ---------------------------------------------------------------------------


def test_review_result_model_has_rule_assessments_column():
    """ReviewResult ORM 模型应有 rule_assessments 列。"""
    from app.models.review_result import ReviewResult

    columns = {c.key for c in ReviewResult.__table__.columns}
    assert "rule_assessments" in columns, "ReviewResult 表缺少 rule_assessments 列"
