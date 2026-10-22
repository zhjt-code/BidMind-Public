"""报告/前端契约字段覆盖护栏（T-11，报告一致性对齐计划）

职责: 锁定 ReviewResultResponse / ReviewSummaryResponse / CollusionResults 的字段集，
      新增字段必须显式登记其消费去向，防止"契约扩字段、报告或前端不同步"再次发生。
不做: 不校验字段语义/取值，仅校验字段是否被登记，以及报告渲染是否真实消费关键新增字段。

登记口径(value): report=仅PDF报告 / frontend=仅结果页 / both=两端 / exempt=内部或开关控制无需展示。
"""
import pytest

from app.core.report.collusion_generator import CollusionReportGenerator
from app.core.report.generator import ReportGenerator
from app.schemas.collusion import (
    AttentionItem,
    CollusionPairResult,
    CollusionResults,
    EvidenceChunk,
    FindingItem,
)
from app.schemas.review import ReviewResultResponse, ReviewSummaryResponse

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 字段登记表
# ---------------------------------------------------------------------------

REVIEW_RESULT_REGISTERED = {
    "id": "exempt", "project_id": "exempt", "requirement_id": "exempt",
    "bid_response_id": "exempt",
    "category": "both", "requirement_summary": "both",
    "requirement_description": "both", "response_summary": "both",
    "response_content": "both",
    "is_compliant": "frontend", "compliance_level": "both", "score": "both",
    "comment": "both", "risk_level": "both",
    "canonical_key": "both", "fact_key": "both", "family_type": "both",
    "attribution_type": "both", "consistency_flag": "both",
    "main_requirement_id": "both", "subject_scope": "both",
    "evidence_relation": "both", "reconcile_note": "both",
    "source_pages": "both", "source_page_numbers": "both",
    "source_document_id": "frontend", "source_parts": "frontend",
    "source_page_locations": "both", "source_page_excerpts": "frontend",
    "requirement_source_page": "both", "requirement_source_pages": "both",
    "requirement_keywords": "frontend", "requirement_sub_requirements": "both",
    "requirement_merged_count": "frontend",
    "response_completeness": "both", "response_missing_elements": "both",
    "pending_judgment": "both",
    "response_linked_to": "frontend", "response_evidence_anchor": "both",
    "relevance_score": "both", "relevance_note": "both",
    "requirement_kind": "both", "max_score": "both", "is_key_review": "both",
    "estimated_score": "both",
    # 评审意义：前端"参考条款·不计分"徽标消费，不进 PDF 报告。
    "review_relevance": "frontend",
    # 评分项卷归属：仅结果页"评分项视图"按卷分组下钻使用，不进 PDF 报告。
    "volume": "frontend", "volume_max": "frontend", "volume_weight": "frontend",
    # 评分卷/分组原文标签：结果页评分卷导航与明细行过滤键消费。
    "volume_label": "frontend",
    # 评分项采购包归属：结果页多包文档按"包·卷"下钻过滤使用。
    "lot": "frontend",
    "retrieval_chunks": "exempt",
    # Wave3-S10 C3: 评分项规则与逐规则判定现已进入 PDF 报告（评分项逐条评审章节）
    "score_item_name": "both",
    "score_rules": "both",
    "deduction_rules": "both",
    "rule_assessments": "both",
    "failure_state": "exempt", "failure_reason": "exempt", "confidence": "exempt",
    "gate_trace": "exempt", "review_required": "exempt", "reviewed_at": "exempt",
    "reviewed_by": "exempt",
}

REVIEW_SUMMARY_REGISTERED = {
    "project_id": "exempt", "total_count": "both", "compliant_count": "both",
    "non_compliant_count": "report", "fully_count": "both", "partially_count": "both",
    "not_count": "both", "missing_count": "both", "high_risk_count": "both",
    "medium_risk_count": "both", "low_risk_count": "both", "total_score": "report",
    "average_score": "report", "weighted_score": "both",
    "scoring_item_count": "both", "estimated_item_count": "both",
    "estimated_total_score": "both", "max_total_score": "both",
    "estimated_score_by_volume": "both", "scoring_total_reliable": "both",
    # 按采购包核计：结果页按包播报 + PDF 报告按包分行渲染。
    "estimated_score_by_lot": "both",
    "conclusion": "both", "conclusion_reason": "both",
}

COLLUSION_RESULTS_REGISTERED = {
    "project_id": "exempt", "overall_risk": "both", "project_decision": "both",
    "project_risk_level": "both", "decision_confidence": "both",
    "review_required": "both", "decision_basis": "exempt",
    "decision_engine_version": "frontend", "overall_risk_basis": "exempt",
    "risk_distribution_basis": "both", "risk_consistency_status": "both",
    "risk_consistency_note": "both", "has_tender_doc": "both",
    "summary_source_version": "exempt", "data_quality_flags": "both",
    "profile_quality_flags": "both", "coverage_stats": "both",
    "industry_profile_snapshot": "exempt", "industry_profile_confidence": "both",
    "industry_profile_status": "frontend", "industry_profile_signals": "exempt",
    "factual_explanation": "both",
    # 事实说明分条列表：前端串标总览分条渲染消费（PDF 沿用 factual_explanation 整段）。
    "factual_explanation_items": "frontend",
    "similarity_matrix": "both", "document_labels": "both", "pair_results": "both",
    "suspected_groups": "both", "synthesis_summary": "both",
    "narrative_summary": "both", "investigation_suggestions": "both",
    "groups": "both", "global_findings": "both", "extraction_warnings": "both",
    "completed_at": "exempt",
    "failure_state": "exempt", "failure_reason": "exempt", "confidence": "exempt",
    "gate_trace": "exempt", "failure_review_required": "exempt",
    "reviewed_at": "exempt", "reviewed_by": "exempt",
}


def _assert_fields_registered(model, registered: dict, name: str) -> None:
    actual = set(model.model_fields.keys())
    declared = set(registered.keys())
    missing = actual - declared
    stale = declared - actual
    assert not missing, (
        f"{name} 新增字段未登记消费去向(report/frontend/both/exempt)。"
        f"请同步报告/前端展示与本护栏: {sorted(missing)}"
    )
    assert not stale, f"{name} 登记表存在已删除字段，请清理: {sorted(stale)}"


def test_review_result_response_fields_registered() -> None:
    _assert_fields_registered(
        ReviewResultResponse, REVIEW_RESULT_REGISTERED, "ReviewResultResponse"
    )


def test_review_summary_response_fields_registered() -> None:
    _assert_fields_registered(
        ReviewSummaryResponse, REVIEW_SUMMARY_REGISTERED, "ReviewSummaryResponse"
    )


def test_collusion_results_fields_registered() -> None:
    _assert_fields_registered(
        CollusionResults, COLLUSION_RESULTS_REGISTERED, "CollusionResults"
    )


# ---------------------------------------------------------------------------
# 行为护栏：关键新增字段必须真实出现在报告渲染中
# ---------------------------------------------------------------------------


def _extract_text(items) -> str:
    parts: list[str] = []
    for item in items:
        getter = getattr(item, "getPlainText", None)
        if callable(getter):
            parts.append(getter())
    return "\n".join(parts)


def test_bid_report_renders_scoring_estimate() -> None:
    """T-01: 评分项预估总得分必须出现在投标报告汇总区。"""
    generator = ReportGenerator()
    summary = {
        "scoring_item_count": 2,
        "estimated_item_count": 2,
        "estimated_total_score": 73.0,
        "max_total_score": 100.0,
        "estimated_score_by_volume": {
            "技术标": {"estimated": 73.0, "max": 100.0, "count": 2}
        },
    }
    text = _extract_text(generator._build_scoring_summary(summary))
    assert "评分项预估得分" in text


def test_bid_report_renders_scoring_by_lot() -> None:
    """: 多采购包文档报告须按包分行核计，禁止出现"多卷/加权"错误归因。"""
    generator = ReportGenerator()
    summary = {
        "scoring_item_count": 35,
        "estimated_item_count": 35,
        "estimated_total_score": 36.5,
        "max_total_score": 200.0,
        "scoring_total_reliable": False,
        "estimated_score_by_volume": {},
        "estimated_score_by_lot": {
            "采购包1": {
                "estimated": 30.0, "max": 100.0, "count": 14, "estimated_count": 14,
                "reliable": True, "unassigned": False,
                "by_volume": {"技术标": {"estimated": 0.0, "max": 70.0, "count": 13}},
            },
            "采购包2": {
                "estimated": 6.5, "max": 100.0, "count": 21, "estimated_count": 21,
                "reliable": True, "unassigned": False,
                "by_volume": {"技术标": {"estimated": 6.5, "max": 90.0, "count": 20}},
            },
        },
    }
    rows_text = "\n".join(
        " ".join(str(cell) for cell in row)
        for row in generator._scoring_rows_by_lot(summary["estimated_score_by_lot"])
    )
    assert "采购包1" in rows_text and "采购包2" in rows_text
    assert "30.0" in rows_text and "6.5" in rows_text
    text = _extract_text(generator._build_scoring_summary(summary))
    assert "按采购包分别评审" in text
    assert "多卷/加权" not in text, "多包文档禁止沿用'多卷/加权/非100分制'错误归因文案"


def test_collusion_pair_detail_renders_attention_crossfield_chunks() -> None:
    """T-02/T-03/T-05: pair 详情必须渲染跨字段串联/关注项/重合片段。"""
    generator = CollusionReportGenerator()
    pair = CollusionPairResult(
        pair_id="1:2", doc_a_id=1, doc_b_id=2,
        doc_a_name="A.pdf", doc_b_name="B.pdf",
        overall_risk="medium",
        d1_personnel_overlap=0.8,
        findings=[
            FindingItem(
                dimension="D8", level="second",
                description="PDF 作者与他方法定代表人一致",
                risk_pattern="author_eq_other_legal_rep",
                source_field_a="metadata_info.author",
                source_field_b="company_info.legal_representative",
                legal_reference="《招标投标法实施条例》第40条第6项",
            )
        ],
        attention_items=[
            AttentionItem(
                category="negative_finding", dimension="D3",
                summary="技术表述相似但属招标模板", source_reason="命中通用白名单",
            )
        ],
        evidence_chunks=[
            EvidenceChunk(
                text_a="片段A原文", text_b="片段B原文",
                page_a=1, page_b=2, score=0.9, boilerplate_flag=True,
            )
        ],
    )
    text = _extract_text(generator._build_single_pair(1, pair))
    assert "规则: author_eq_other_legal_rep" in text
    assert "法规对照" in text
    assert "关注项" in text
    assert "重合片段" in text


def test_bid_report_renders_scoring_detail_section() -> None:
    """T-C3: 评分项逐条评审章节必须出现在投标报告 story 中，含章节标题。"""
    from app.core.report.scoring_detail_section import build_scoring_detail_story

    generator = ReportGenerator()
    results = [
        {
            "requirement_kind": "scoring_item",
            "score_item_name": "技术方案评分",
            "max_score": 20.0,
            "estimated_score": 18.0,
            "rule_assessments": [
                {
                    "rule_excerpt": "须提供完整实施路径",
                    "status": "met",
                    "points": 18.0,
                    "reason": "方案完整，符合评分要求",
                    "evidence_pages": [5],
                }
            ],
            "response_content": "投标人提供了详细技术方案。",
        }
    ]
    story = build_scoring_detail_story(results, "Helvetica", generator.styles)
    assert len(story) > 0
    text = _extract_text(story)
    assert "评分项逐条评审" in text


def test_collusion_group_conclusion_renders_factual_and_narrative() -> None:
    """T-04: group 综合结论必须含 narrative 与 factual 说明。"""
    generator = CollusionReportGenerator()
    results = CollusionResults(
        project_id=1, overall_risk="medium", project_decision="suspicious_found",
        decision_basis="groups", overall_risk_basis="groups",
        risk_distribution_basis="groups",
        narrative_summary="项目级裁决说明文本。",
        factual_explanation="覆盖率 100%，无降级。",
        synthesis_summary="综合研判补充说明。",
        groups=[
            {
                "group_index": 0, "member_doc_ids": [1, 2],
                "member_names": ["A.pdf", "B.pdf"], "risk_level": "medium",
                "related_pair_ids": ["1:2"],
            }
        ],
    )
    text = _extract_text(generator._build_group_conclusion(results))
    assert "项目级结论" in text
    assert "事实说明" in text
