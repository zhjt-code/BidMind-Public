"""串标审查 ground truth schema。

字段对应 docs/regression_sample_spec.md §3.2。
"""

from pydantic import BaseModel, Field

from .common import EvidenceLevelEnum, FailureStateEnum, RiskLevelEnum


class VerifiedFinding(BaseModel):
    """单条 finding 的验证信息。"""

    page: int | None = None
    quote: str = ""
    verified: bool = False


class CollusionExpected(BaseModel):
    """单对 pair 的串标审查 ground truth。"""

    pair_id: str = Field(..., description="doc_a_id:doc_b_id")

    expected_overall_risk: RiskLevelEnum = Field(
        ..., description="专家标注总体风险等级"
    )

    expected_d1_score: float = Field(0.0, ge=0, le=1)
    expected_d2_score: float = Field(0.0, ge=0, le=1)
    expected_d3_score: float = Field(0.0, ge=0, le=1)
    expected_d4_score: float = Field(0.0, ge=0, le=1)
    expected_d5_score: float = Field(0.0, ge=0, le=1)
    expected_d6_score: float = Field(0.0, ge=0, le=1)
    expected_d7_score: float = Field(0.0, ge=0, le=1)
    expected_d8_score: float = Field(0.0, ge=0, le=1)

    expected_d1_evidence_level: EvidenceLevelEnum | None = None
    expected_d2_evidence_level: EvidenceLevelEnum | None = None
    expected_d3_evidence_level: EvidenceLevelEnum | None = None
    expected_d4_evidence_level: EvidenceLevelEnum | None = None
    expected_d5_evidence_level: EvidenceLevelEnum | None = None
    expected_d6_evidence_level: EvidenceLevelEnum | None = None
    expected_d7_evidence_level: EvidenceLevelEnum | None = None
    expected_d8_evidence_level: EvidenceLevelEnum | None = None

    expected_verified_findings: list[VerifiedFinding] = Field(
        default_factory=list, description="每条 finding 的 page/quote 可验证性"
    )

    expected_failure_state: FailureStateEnum = Field(
        default=FailureStateEnum.COMPLETED,
        description="理想失败合同状态",
    )

    is_false_positive_candidate: bool = Field(
        default=False, description="容易误报的样本"
    )
    expert_note: str = Field(default="", description="标注理由")
    bug_trace: str | None = Field(
        default=None, description="Level 3 来源 BUG 编号"
    )
