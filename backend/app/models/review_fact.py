"""审查事实图模型（事实唯一 + 多视图映射）。"""

from sqlalchemy import (
    Column,
    String,
    Integer,
    ForeignKey,
    Text,
    Float,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class ReviewFact(BaseModel):
    """事实主表：同一证据事实在项目内唯一。"""

    __tablename__ = "review_facts"
    __table_args__ = (
        UniqueConstraint("project_id", "fact_uid", name="uq_review_fact_project_uid"),
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID",
    )
    fact_uid = Column(
        String(255),
        nullable=False,
        index=True,
        comment="事实唯一键(topic+anchor_signature)",
    )
    topic = Column(String(50), nullable=True, comment="主题")
    family_type = Column(String(30), nullable=True, comment="证据族")
    anchor_text = Column(Text, nullable=True, comment="事实锚点文本")
    source_pages = Column(String(500), nullable=True, comment="事实页码簇")
    confidence = Column(Float, nullable=True, comment="事实置信度(0-1)")
    primary_compliance_level = Column(String(20), nullable=True, comment="事实主判定-符合程度")
    primary_score = Column(Integer, nullable=True, comment="事实主判定-评分")
    primary_risk_level = Column(String(20), nullable=True, comment="事实主判定-风险等级")
    primary_note = Column(Text, nullable=True, comment="事实主判定说明")
    status = Column(
        String(20),
        nullable=True,
        comment="事实状态: aligned/needs_review/conflicted",
    )

    project = relationship("Project")
    links = relationship(
        "ReviewFactLink",
        back_populates="fact",
        cascade="all, delete-orphan",
    )
    judgements = relationship(
        "ReviewFactJudgement",
        back_populates="fact",
        cascade="all, delete-orphan",
    )


class ReviewFactLink(BaseModel):
    """事实映射表：事实与要求/响应的多对多关联。"""

    __tablename__ = "review_fact_links"

    fact_id = Column(
        Integer,
        ForeignKey("review_facts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="事实ID",
    )
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="关联要求ID",
    )
    bid_response_id = Column(
        Integer,
        ForeignKey("bid_responses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="关联响应ID",
    )
    category = Column(String(30), nullable=True, comment="类别视图")
    subject_scope = Column(String(20), nullable=True, comment="主体范围")
    link_role = Column(String(20), nullable=False, default="pair", comment="映射角色")
    evidence_anchor = Column(String(255), nullable=True, comment="映射证据锚点")
    source_pages = Column(String(500), nullable=True, comment="映射来源页码")

    fact = relationship("ReviewFact", back_populates="links")
    requirement = relationship("Requirement")
    bid_response = relationship("BidResponse")


class ReviewFactJudgement(BaseModel):
    """事实视图判定表：事实投影到类别视图后的判定快照。"""

    __tablename__ = "review_fact_judgements"

    fact_id = Column(
        Integer,
        ForeignKey("review_facts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="事实ID",
    )
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="要求ID",
    )
    review_result_id = Column(
        Integer,
        ForeignKey("review_results.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="审查结果ID",
    )
    view_category = Column(String(30), nullable=True, comment="视图类别")
    compliance_level = Column(String(20), nullable=True, comment="符合程度")
    score = Column(Integer, nullable=True, comment="评分")
    risk_level = Column(String(20), nullable=True, comment="风险等级")
    is_primary = Column(Integer, nullable=False, default=0, comment="是否主判定")
    note = Column(Text, nullable=True, comment="映射说明")

    fact = relationship("ReviewFact", back_populates="judgements")
    requirement = relationship("Requirement")
    review_result = relationship("ReviewResult")
