"""投标审查 ground truth schema。

字段对应 docs/regression_sample_spec.md §3.1。
"""

from pydantic import BaseModel, Field

from .common import ComplianceLevelEnum, FailureStateEnum, RiskLevelEnum


class InvestigateExpected(BaseModel):
    """单条投标审查结果的 ground truth。"""

    requirement_id: int = Field(..., description="关联招标要求 ID")

    expected_compliance_level: ComplianceLevelEnum = Field(
        ..., description="人工标注正确结论"
    )
    expected_is_compliant: bool = Field(..., description="人工标注是否合规")

    expected_risk_level: RiskLevelEnum = Field(
        ..., description="人工标注风险等级"
    )
    expected_relevance_score: int = Field(
        ..., ge=0, le=100, description="人工标注相关性评分 0-100"
    )
    expected_evidence_pages: list[int] = Field(
        default_factory=list, description="人工核验的真实页码列表"
    )

    expected_failure_state: FailureStateEnum = Field(
        default=FailureStateEnum.COMPLETED,
        description="理想失败合同状态",
    )

    is_critical: bool = Field(
        default=False,
        description="是否关键样本（否决项/高金额/高风险）",
    )
    expert_note: str = Field(default="", description="标注理由")
    bug_trace: str | None = Field(
        default=None, description="Level 3 来源 BUG 编号"
    )
