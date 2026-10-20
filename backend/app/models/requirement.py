"""招标要求模型"""

import enum

from sqlalchemy import JSON, Column, Enum, Float, ForeignKey, Integer, String, Text, or_
from sqlalchemy.orm import relationship
from sqlalchemy.sql.elements import ColumnElement

from app.models.base import BaseModel


class RequirementCategory(str, enum.Enum):
    """招标要求分类"""
    QUAL = "qualification"  # 资质要求
    TECH = "technical"  # 技术要求
    COMM = "commercial"  # 商务要求
    PERF = "performance"  # 业绩要求
    STAFF = "staffing"  # 人员要求
    FORMAT = "format"  # 格式要求


class Requirement(BaseModel):
    """招标要求模型:存储从招标文件中提取的结构化要求
    
    核心字段:
    - category: 要求分类(资质/技术/商务/业绩/人员/格式)
    - description: 要求描述
    - is_mandatory: 是否为否决项
    - confirmed: 是否经过人工确认(必须)
    """
    
    __tablename__ = "requirements"
    
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID"
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="来源文档ID(招标文件)"
    )
    category = Column(
        Enum(RequirementCategory),
        nullable=False,
        comment="要求分类"
    )
    description = Column(Text, nullable=False, comment="要求描述")
    is_mandatory = Column(
        Integer,
        default=0,
        nullable=False,
        comment="是否为否决项:0/1"
    )
    source_page = Column(Integer, nullable=True, comment="来源页码")
    source_pages = Column(Text, nullable=True, comment="来源页码列表(JSON)")
    keywords = Column(Text, nullable=True, comment="关键词列表(JSON)")
    sub_requirements = Column(Text, nullable=True, comment="子要求列表(JSON)")
    merged_count = Column(Integer, nullable=True, comment="合并数量")
    topic = Column(
        String(50),
        nullable=True,
        comment="主题标记: performance/qualification/technical/...",
    )
    subject_scope = Column(
        String(20),
        nullable=True,
        comment="主体范围: enterprise/personnel/both/generic",
    )
    canonical_key = Column(
        String(255),
        nullable=True,
        index=True,
        comment="事实键（topic+signature，不含主体）",
    )
    main_requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="主判定要求ID",
    )
    review_relevance = Column(
        String(20),
        default="actionable",
        nullable=False,
        server_default="actionable",
        comment="评审意义分级: actionable/informational/procedural",
    )
    relevance_reason = Column(
        String(40),
        nullable=True,
        comment="评审意义归因: llm_locus/rule_floor/contract_section/behavioral_mandatory/manual",
    )
    requirement_kind = Column(
        String(20),
        default="normal",
        nullable=False,
        server_default="normal",
        comment="要求种类: normal(普通要求)/scoring_item(评分项)",
    )
    score_weight = Column(
        Float,
        nullable=True,
        comment="评分项满分分值(仅 scoring_item)",
    )
    is_key_review = Column(
        Integer,
        default=0,
        nullable=False,
        server_default="0",
        comment="是否重点评审项(评分项默认1): 0/1",
    )
    scoring_metadata = Column(
        JSON,
        nullable=True,
        comment="评分项结构化元数据(JSON): 评分项名/分档规则/扣分规则/所属卷等",
    )
    confirmed = Column(
        Integer,
        default=0,
        nullable=False,
        comment="是否已人工确认:0/1"
    )
    
    # 关系
    project = relationship("Project", back_populates="requirements")
    document = relationship("Document", back_populates="requirements")
    bid_responses = relationship(
        "BidResponse",
        back_populates="requirement",
        cascade="all, delete-orphan"
    )
    review_results = relationship(
        "ReviewResult",
        back_populates="requirement",
        foreign_keys="ReviewResult.requirement_id",
        cascade="all, delete-orphan"
    )
    main_requirement = relationship(
        "Requirement",
        remote_side="Requirement.id",
        foreign_keys=[main_requirement_id],
        post_update=True,
    )
    
    def __repr__(self) -> str:
        return f"<Requirement(id={self.id}, category='{self.category}', mandatory={self.is_mandatory})>"


def non_manual_scoring_metadata_filter() -> ColumnElement[bool]:
    """NULL 安全的"非手工评分项"过滤条件。

 自动评分项的 scoring_metadata 不含 source 键, JSON 取值为 SQL NULL;
 裸 `!= 'manual'` 在三值逻辑下对 NULL 不为 TRUE, 会把全部自动项漏出清理范围。
 统一用 IS DISTINCT FROM 收口, 重提取清理与提取幂等清理必须共用本条件。
 """
    return or_(
        Requirement.scoring_metadata.is_(None),
        Requirement.scoring_metadata["source"].as_string().is_distinct_from("manual"),
    )
