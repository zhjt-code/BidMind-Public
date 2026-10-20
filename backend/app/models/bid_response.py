"""投标响应模型"""

from sqlalchemy import Column, String, Integer, ForeignKey, Enum, Text, JSON
from sqlalchemy.orm import relationship
import enum
from app.models.base import BaseModel


class EvidenceType(str, enum.Enum):
    """证明材料类型"""
    CERTIFICATE = "certificate"  # 证书
    PROPOSAL = "proposal"  # 方案
    COMMITMENT = "commitment"  # 承诺函
    REFERENCE = "reference"  # 业绩证明
    OTHER = "other"  # 其他


class BidResponse(BaseModel):
    """投标响应模型:存储从投标文件中提取的响应内容
    
    核心关系:
    - 一条招标要求 → 一条或多条投标响应
    - 每条响应关联到具体的文档分块
    """
    
    __tablename__ = "bid_responses"
    
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="对应的招标要求ID"
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="来源文档ID(投标文件)"
    )
    source_chunk_id = Column(
        Integer,
        ForeignKey("chunks.id", ondelete="SET NULL"),
        nullable=True,
        # Fix-02/Fix-06 deprecated: 多路检索命中集合见 bid_response_chunks 表，
        # 这里只保留首个命中以兼容历史数据与老视图；新代码请消费 retrieval_chunks 关系。
        comment="来源分块ID（deprecated: 使用 bid_response_chunks 表）",
    )
    content = Column(Text, nullable=False, comment="响应内容摘要")
    evidence_type = Column(
        Enum(EvidenceType),
        nullable=True,
        comment="证明材料类型"
    )
    source_pages = Column(String(500), nullable=True, comment="来源页码(逗号分隔)")
    completeness = Column(
        String(20),
        nullable=True,
        comment="响应完整性: full/partial/form_only",
    )
    missing_elements = Column(
        Text,
        nullable=True,
        comment="缺失要素(JSON字符串)",
    )
    linked_to = Column(
        Integer,
        nullable=True,
        comment="关联到主响应的requirement_id",
    )
    evidence_anchor = Column(
        String(255),
        nullable=True,
        comment="证据锚点(合同号/项目名等)",
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
    facet_states = Column(
        JSON,
        nullable=True,
        comment="分面闭合状态JSON",
    )
    
    # 关系
    requirement = relationship("Requirement", back_populates="bid_responses")
    document = relationship("Document", back_populates="bid_responses")
    # Fix-02 / 问题 3: source_chunk_id 为多路检索首个命中的兼容外键，
    # 详细命中集合见 retrieval_chunks；新代码应优先消费 retrieval_chunks。
    source_chunk = relationship("Chunk", back_populates="bid_responses")
    retrieval_chunks = relationship(
        "BidResponseChunk",
        back_populates="bid_response",
        cascade="all, delete-orphan",
        order_by="BidResponseChunk.id",
    )
    review_results = relationship(
        "ReviewResult",
        back_populates="bid_response",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self) -> str:
        return f"<BidResponse(id={self.id}, req_id={self.requirement_id}, evidence='{self.evidence_type}')>"
