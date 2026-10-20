"""审查结果模型"""

from sqlalchemy import Column, DateTime, Float, SmallInteger, String, Integer, ForeignKey, Enum, JSON, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
import enum
from app.models.base import BaseModel


class ComplianceLevel(str, enum.Enum):
    """符合程度"""
    FULLY = "fully"  # 完全符合
    PARTIALLY = "partially"  # 部分符合
    NOT = "not"  # 不符合
    MISSING = "missing"  # 缺失响应


class RiskLevel(str, enum.Enum):
    """风险等级"""
    NONE = "none"  # 无风险
    LOW = "low"  # 低风险
    MEDIUM = "medium"  # 中风险
    HIGH = "high"  # 高风险


class ReviewResult(BaseModel):
    """审查结果模型:存储AI对比审查的结论
    
    核心逻辑:
    - 对每条招标要求,AI审查其对应的投标响应
    - 给出符合程度、评分、意见、风险等级
    """
    
    __tablename__ = "review_results"
    __table_args__ = (
        UniqueConstraint("project_id", "requirement_id", name="uq_review_project_requirement"),
    )
    
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID"
    )
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="招标要求ID"
    )
    bid_response_id = Column(
        Integer,
        ForeignKey("bid_responses.id", ondelete="CASCADE"),
        nullable=True,
        comment="投标响应ID(如果找不到响应则为NULL)"
    )
    requirement_summary = Column(Text, nullable=False, comment="招标要求摘要")
    response_summary = Column(Text, nullable=True, comment="投标响应摘要")
    is_compliant = Column(
        Integer,
        default=0,
        nullable=False,
        comment="是否符合:0/1"
    )
    compliance_level = Column(
        Enum(ComplianceLevel),
        nullable=False,
        comment="符合程度"
    )
    score = Column(Integer, nullable=False, comment="评分(0-100)")
    estimated_score = Column(
        Float,
        nullable=True,
        comment="评分项 AI 预估得分(仅 scoring_item;满分见 requirement.score_weight)",
    )
    comment = Column(Text, nullable=False, comment="详细审查意见")
    risk_level = Column(
        Enum(RiskLevel),
        default=RiskLevel.NONE,
        nullable=False,
        comment="风险等级"
    )
    canonical_key = Column(
        String(255),
        nullable=True,
        index=True,
        comment="跨类别归并键",
    )
    fact_key = Column(
        String(255),
        nullable=True,
        index=True,
        comment="事实键（topic+signature，不含主体）",
    )
    family_type = Column(
        String(30),
        nullable=True,
        index=True,
        comment="证据族类型: performance/audit_finance/qualification/generic",
    )
    attribution_type = Column(
        String(20),
        nullable=True,
        comment="归因关系: shared/independent/none",
    )
    evidence_relation = Column(
        String(20),
        nullable=True,
        comment="证据关系: shared/independent/undetermined",
    )
    consistency_flag = Column(
        String(20),
        nullable=True,
        comment="一致性标记: aligned/needs_review/conflicted/unchecked",
    )
    reconcile_note = Column(
        Text,
        nullable=True,
        comment="一致性收敛解释",
    )
    main_requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="主判定要求ID",
    )
    relevance_score = Column(
        Integer,
        nullable=True,
        comment="领域关联度 0-100",
    )
    relevance_note = Column(
        String(500),
        nullable=True,
        comment="关联性说明",
    )

    # Wave2-S5 P1 B3: 逐规则判定结果（JSONB 列，老数据为 null）
    rule_assessments = Column(
        JSONB,
        nullable=True,
        comment="评分项逐规则判定列表 [{rule_excerpt,status,points,reason,evidence_pages}]",
    )

    # B1: 失败合同字段（failure_contract §4.1）
    failure_state = Column(String(32), nullable=False, server_default="completed", comment="五状态枚举")
    failure_reason = Column(Text, nullable=True, comment="人类可读失败原因")
    confidence = Column(Float, nullable=True, comment="Gate 综合置信度 0.0-1.0")
    gate_trace = Column(JSON, nullable=True, comment="每道 Gate verdict 列表")
    review_required = Column(SmallInteger, nullable=False, server_default="0", comment="是否需人工复核")
    reviewed_at = Column(DateTime(timezone=True), nullable=True, comment="人工复核完成时间")
    reviewed_by = Column(Integer, nullable=True, comment="复核人 user_id")

    # 关系
    project = relationship("Project", back_populates="review_results")
    requirement = relationship(
        "Requirement",
        back_populates="review_results",
        foreign_keys=[requirement_id],
    )
    bid_response = relationship("BidResponse", back_populates="review_results")
    
    def __repr__(self) -> str:
        return f"<ReviewResult(id={self.id}, compliant={self.is_compliant}, level='{self.compliance_level}', score={self.score})>"
