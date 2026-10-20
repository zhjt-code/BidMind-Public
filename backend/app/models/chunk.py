"""文本分块模型(含向量)"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Chunk(BaseModel):
    """文本分块模型:存储文档分块及其向量表示
    
    支持语义检索:
    - content: 分块文本内容
    - embedding: 1024维向量(bge-m3模型)
    """
    
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_fingerprint",
            name="uq_chunks_document_fingerprint",
        ),
    )
    
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属文档ID"
    )
    content = Column(Text, nullable=False, comment="分块内容")
    embedding = Column(
        Vector(1024),  # bge-m3模型输出1024维向量
        nullable=True,
        comment="文本向量(用于语义检索)"
    )
    chunk_type = Column(
        String(32),
        nullable=False,
        default="text",
        comment="分块类型(text/image_annotation/image_evidence)"
    )
    parent_block_id = Column(String(128), nullable=True, comment="父级语义块ID")
    section_path = Column(Text, nullable=True, comment="章节/条款路径")
    chunk_fingerprint = Column(String(64), nullable=False, index=True, comment="幂等指纹")
    embedding_version = Column(String(64), nullable=False, default="bge-m3-v1", comment="向量版本")
    page_num = Column(Integer, nullable=True, comment="来源页码")
    chunk_index = Column(Integer, nullable=False, comment="分块序号(从0开始)")
    char_count = Column(Integer, nullable=False, comment="字符数")
    # 阶段1.5(evidence bundle 最小版): 结构化载荷, 评分提取直接消费网格而非重解析 markdown
    chunk_metadata = Column(
        JSONB, nullable=True,
        comment="结构化载荷(scoring_payload: 网格/lot/表格指纹/页范围)",
    )
    
    # 关系
    document = relationship("Document", back_populates="chunks")
    bid_responses = relationship(
        "BidResponse",
        back_populates="source_chunk"
    )
    
    def __repr__(self) -> str:
        return f"<Chunk(id={self.id}, doc_id={self.document_id}, page={self.page_num}, index={self.chunk_index})>"
