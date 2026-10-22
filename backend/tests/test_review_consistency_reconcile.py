import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/bidmind_lite",
)

from app.core.ai.reviewer import ReviewOrchestrator
from app.core.extractor.tender_extractor import TenderExtractor
from app.models.bid_response import BidResponse
from app.models.requirement import Requirement
from app.models.review_result import ComplianceLevel, ReviewResult, RiskLevel


class _DummyLLM:
    async def chat(self, *args, **kwargs):  # pragma: no cover
        return "{}"


def test_fact_key_not_include_scope_suffix():
    extractor = TenderExtractor(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
    key = extractor._infer_canonical_key(  # noqa: SLF001
        "投标人提供2023年合同编号HT-2023-001的类似项目业绩证明"
    )
    assert key.startswith("performance:")
    # 事实键不再拼接 :enterprise/:personnel/:both
    assert key.count(":") == 1


def test_relation_shared_when_anchor_overlap():
    reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
    req1 = Requirement(description="业绩要求", subject_scope="enterprise")
    req2 = Requirement(description="项目经理业绩要求", subject_scope="personnel")
    rr1 = ReviewResult(compliance_level=ComplianceLevel.FULLY, score=95, is_compliant=1, comment="", risk_level=RiskLevel.LOW)
    rr2 = ReviewResult(compliance_level=ComplianceLevel.NOT, score=40, is_compliant=0, comment="", risk_level=RiskLevel.MEDIUM)
    br1 = BidResponse(content="合同编号HT-2023-001", evidence_anchor="HT-2023-001", source_pages="12,13")
    br2 = BidResponse(content="该人员参与合同HT-2023-001", evidence_anchor="HT-2023-001", source_pages="13,14")
    relation = reviewer._compute_evidence_relation([(rr1, req1, br1), (rr2, req2, br2)])  # noqa: SLF001
    assert relation == "shared"


def test_relation_undetermined_when_only_scope_cross():
    reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
    req1 = Requirement(description="企业要求", subject_scope="enterprise")
    req2 = Requirement(description="人员要求", subject_scope="personnel")
    rr1 = ReviewResult(compliance_level=ComplianceLevel.FULLY, score=95, is_compliant=1, comment="", risk_level=RiskLevel.LOW)
    rr2 = ReviewResult(compliance_level=ComplianceLevel.NOT, score=40, is_compliant=0, comment="", risk_level=RiskLevel.MEDIUM)
    relation = reviewer._compute_evidence_relation([(rr1, req1, None), (rr2, req2, None)])  # noqa: SLF001
    assert relation == "undetermined"


def test_single_fact_defaults_independent():
    reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
    req = Requirement(description="单条事实要求", subject_scope="generic")
    rr = ReviewResult(compliance_level=ComplianceLevel.FULLY, score=95, is_compliant=1, comment="", risk_level=RiskLevel.LOW)
    relation = reviewer._compute_evidence_relation([(rr, req, None)])  # noqa: SLF001
    assert relation == "independent"


def test_performance_dual_scope_promotes_to_shared_without_conflict():
    reviewer = ReviewOrchestrator(db=None, llm_client=_DummyLLM())  # type: ignore[arg-type]
    req1 = Requirement(id=1, description="企业业绩要求", subject_scope="enterprise")
    req2 = Requirement(id=2, description="人员业绩要求", subject_scope="personnel")
    rr1 = ReviewResult(
        id=1,
        requirement_id=1,
        compliance_level=ComplianceLevel.FULLY,
        score=95,
        is_compliant=1,
        comment="",
        risk_level=RiskLevel.LOW,
        family_type="performance",
        fact_key="performance:test",
    )
    rr2 = ReviewResult(
        id=2,
        requirement_id=2,
        compliance_level=ComplianceLevel.PARTIALLY,
        score=75,
        is_compliant=1,
        comment="",
        risk_level=RiskLevel.MEDIUM,
        family_type="performance",
        fact_key="performance:test",
    )
    reviewer._judge_fact_then_project({"performance:test": [(rr1, req1, None), (rr2, req2, None)]})  # noqa: SLF001
    assert rr1.evidence_relation == "shared"
    assert rr2.evidence_relation == "shared"
    assert rr1.consistency_flag == "aligned"
