"""审查无有效结论不得混入符合程度轴。

职责: 锁定 API 出口在脱敏前写出 pending_judgment，且只认失败标记。
不做: 不把 needs_review / 真未响应收进待判定。
"""

from types import SimpleNamespace

from app.core.ai.reviewer_parts.result_builder import FAILURE_COMMENT_MARKER
from app.models.review_result import ComplianceLevel, RiskLevel
from app.services.review_service import _build_review_result_item


def _stub_result(**overrides: object) -> SimpleNamespace:
    fields = dict(
        id=1,
        project_id=1,
        requirement_id=1,
        bid_response_id=7,
        requirement_summary="资质要求",
        response_summary="",
        is_compliant=0,
        compliance_level=ComplianceLevel.MISSING,
        score=0,
        comment="",
        estimated_score=None,
        risk_level=RiskLevel.HIGH,
        main_requirement_id=None,
        relevance_score=None,
        relevance_note=None,
        consistency_flag=None,
        evidence_relation=None,
        reconcile_note=None,
        canonical_key=None,
        fact_key=None,
        family_type=None,
        attribution_type=None,
        rule_assessments=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_timeout_fallback_exports_pending_judgment_and_strips_marker():
    item = _build_review_result_item(
        _stub_result(
            comment=f"审查失败[{FAILURE_COMMENT_MARKER}]：模型调用超时或输出不合规，请人工复核",
        )
    )

    assert item["pending_judgment"] is True
    assert item["compliance_level"] == "missing"
    assert FAILURE_COMMENT_MARKER not in item["comment"]
    assert "审查失败" in item["comment"]


def test_extraction_failure_same_marker_also_pending_judgment():
    """同形态第二例：提取失败兜底，不是超时，但仍无有效符合结论。"""
    item = _build_review_result_item(
        _stub_result(
            comment=f"投标响应提取失败[{FAILURE_COMMENT_MARKER}]：输出不合规，请人工复核",
            requirement_summary="业绩合同",
        )
    )

    assert item["pending_judgment"] is True
    assert FAILURE_COMMENT_MARKER not in item["comment"]


def test_true_missing_without_marker_is_not_pending_judgment():
    item = _build_review_result_item(
        _stub_result(comment="投标文件未找到对应响应，判定为未响应。")
    )

    assert item["pending_judgment"] is False
    assert item["compliance_level"] == "missing"


def test_needs_review_without_marker_is_not_pending_judgment():
    item = _build_review_result_item(
        _stub_result(
            compliance_level=ComplianceLevel.FULLY,
            is_compliant=1,
            comment="已响应，但交叉证据需人工复核一致性。",
            consistency_flag="needs_review",
        )
    )

    assert item["pending_judgment"] is False
    assert item["compliance_level"] == "fully"
