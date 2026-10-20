import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/bidmind_lite",
)

from app.core.ai.reviewer import ReviewOrchestrator
from app.core.matcher.requirement_matcher import MatchedPair
from app.core.review_contracts import (
    ReviewEvidenceBundle,
    ReviewSummaryItem,
    build_review_summary,
    parse_json_list,
    parse_page_numbers,
)
from app.models.bid_response import BidResponse, EvidenceType
from app.models.requirement import Requirement, RequirementCategory
from app.schemas.requirement import RequirementResponse


class _DummyLLM:
    async def chat(self, *args, **kwargs):  # pragma: no cover
        return "{}"


def test_parse_page_numbers_supports_ranges_and_dedup():
    assert parse_page_numbers("1, 2, 4-6, 6, x") == [1, 2, 4, 5, 6]


def test_parse_json_list_supports_json_string():
    assert parse_json_list('["资质", "年限"]') == ["资质", "年限"]


def test_build_review_summary_uses_weighted_score_truth():
    summary = build_review_summary(
        project_id=10,
        items=[
            ReviewSummaryItem(
                category="technical",
                is_compliant=True,
                compliance_level="fully",
                risk_level="low",
                score=90,
            ),
            ReviewSummaryItem(
                category="commercial",
                is_compliant=True,
                compliance_level="fully",
                risk_level="low",
                score=60,
            ),
        ],
    )

    assert round(summary["average_score"], 1) == 75.0
    assert round(summary["weighted_score"], 1) == 39.0
    assert summary["conclusion"] == "fail"


def test_requirement_response_coerces_structured_json_fields():
    response = RequirementResponse.model_validate(
        {
            "id": 1,
            "project_id": 2,
            "document_id": 3,
            "category": "technical",
            "description": "提供完整技术方案",
            "is_mandatory": True,
            "source_page": 6,
            "source_pages": "[6, 7, 8]",
            "keywords": '["技术方案", "完整"]',
            "sub_requirements": '["实施路径", "质量控制"]',
            "merged_count": 2,
            "confirmed": True,
        }
    )

    assert response.source_pages == [6, 7, 8]
    assert response.keywords == ["技术方案", "完整"]
    assert response.sub_requirements == ["实施路径", "质量控制"]
    assert response.merged_count == 2


def test_build_review_prompt_consumes_real_bidresponse_contract():
    reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
    requirement = Requirement(
        id=1,
        category=RequirementCategory.TECH,
        description="投标人需提供完整技术实施方案与质量控制措施",
        is_mandatory=1,
    )
    response = BidResponse(
        id=5,
        requirement_id=1,
        content="已提供实施方案，但未说明质量控制机制",
        evidence_type=EvidenceType.PROPOSAL,
        source_pages="12,13",
        completeness="partial",
        missing_elements='["质量控制机制"]',
        linked_to=8,
        evidence_anchor="TECH-2026-001",
        relevance_score=82,
        relevance_note="同领域方案",
    )
    pair = MatchedPair(requirement=requirement, response=response)
    bundle = ReviewEvidenceBundle(
        response_content=response.content,
        source_pages=[12, 13],
        completeness="partial",
        missing_elements=["质量控制机制"],
        linked_to=8,
        evidence_type="proposal",
        evidence_anchor="TECH-2026-001",
        relevance_score=82,
        relevance_note="同领域方案",
        image_evidence="",
        cross_category_evidence="",
        quality_flags=["missing_gap_details"],
    )

    prompt = reviewer._build_review_prompt(pair, bundle)  # noqa: SLF001

    assert "完整性: partial" in prompt
    assert "缺失要素: 质量控制机制" in prompt
    assert "关联到其他要求: 8" in prompt
    assert "证据锚点: TECH-2026-001" in prompt
    assert "提取阶段相关性分数: 82" in prompt
    assert "missing_gap_details" in prompt
