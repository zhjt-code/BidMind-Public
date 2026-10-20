"""知识库文档模型。"""

from __future__ import annotations

import enum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class KnowledgeFlow(str, enum.Enum):
    """知识库适用业务流程。"""

    bid_review = "bid_review"
    collusion_check = "collusion_check"
    bid_writing = "bid_writing"


class KnowledgeAuthority(str, enum.Enum):
    """知识来源权威等级。"""

    national_standard = "national_standard"
    industry_standard = "industry_standard"
    enterprise_standard = "enterprise_standard"
    user_experience = "user_experience"


class KnowledgeReviewStatus(str, enum.Enum):
    """知识资料审核状态。"""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class KnowledgeOwnerScope(str, enum.Enum):
    """知识资料归属范围。"""

    personal = "personal"
    organization = "organization"
    system = "system"


class KbDocument(BaseModel):
    """组织级长期知识资料。"""

    __tablename__ = "kb_documents"

    owner_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="上传/归属用户",
    )
    title = Column(String(500), nullable=False, index=True, comment="知识资料标题")
    filename = Column(String(255), nullable=False, comment="原始文件名")
    file_path = Column(String(500), nullable=False, comment="文件存储路径")
    file_size = Column(Integer, nullable=False, comment="文件大小(字节)")
    mime_type = Column(String(100), nullable=True, comment="MIME类型")
    content_text = Column(Text, nullable=True, comment="解析后的全文")
    page_count = Column(Integer, nullable=True, comment="页数或估算页数")

    applicable_flows = Column(JSON, nullable=False, default=list, comment="适用流程数组")
    business_type = Column(String(64), nullable=False, index=True, comment="业务大类")
    business_label = Column(String(100), nullable=True, comment="业务细分标签")
    valid_from = Column(Date, nullable=True, comment="有效期起始")
    valid_to = Column(Date, nullable=True, index=True, comment="有效期截止")
    source = Column(String(255), nullable=True, comment="来源说明")
    authority = Column(
        String(32),
        nullable=False,
        default=KnowledgeAuthority.user_experience.value,
        index=True,
        comment="权威等级",
    )
    enabled = Column(Boolean, nullable=False, default=True, index=True, comment="是否启用")
    version = Column(String(64), nullable=False, default="1", comment="版本号")
    superseded_by_id = Column(
        Integer,
        ForeignKey("kb_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="被替代的新版本ID",
    )
    owner_scope = Column(
        String(32),
        nullable=False,
        default=KnowledgeOwnerScope.personal.value,
        index=True,
        comment="归属范围",
    )
    industry_tags = Column(JSON, nullable=False, default=list, comment="行业标签")
    region_tags = Column(JSON, nullable=False, default=list, comment="地区标签")
    review_status = Column(
        String(32),
        nullable=False,
        default=KnowledgeReviewStatus.approved.value,
        index=True,
        comment="审核状态",
    )
    content_fingerprint = Column(
        String(64),
        nullable=False,
        index=True,
        comment="内容指纹",
    )
    processed_at = Column(DateTime(timezone=True), nullable=True, comment="完成索引时间")
    processing_status = Column(
        String(20),
        nullable=False,
        default="saved",
        index=True,
        comment="处理状态:saved/processing/completed/failed",
    )
    error_message = Column(String(1000), nullable=True, comment="处理失败原因")

    owner = relationship("User")
    chunks = relationship(
        "KbChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    atoms = relationship(
        "KbAtom",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    superseded_by = relationship(
        "KbDocument",
        remote_side="KbDocument.id",
        foreign_keys=[superseded_by_id],
    )

    def __repr__(self) -> str:
        return f"<KbDocument(id={self.id}, title='{self.title}', version='{self.version}')>"


class KbChunk(BaseModel):
    """知识库文本分块及向量。"""

    __tablename__ = "kb_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_fingerprint",
            name="uq_kb_chunks_document_fingerprint",
        ),
    )

    document_id = Column(
        Integer,
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属知识资料ID",
    )
    content = Column(Text, nullable=False, comment="分块内容")
    embedding = Column(Vector(1024), nullable=True, comment="bge-m3 1024维向量")
    chunk_type = Column(String(32), nullable=False, default="text", comment="分块类型")
    parent_block_id = Column(String(128), nullable=True, comment="父级语义块ID")
    section_path = Column(Text, nullable=True, comment="章节路径")
    chunk_fingerprint = Column(String(64), nullable=False, index=True, comment="幂等指纹")
    embedding_version = Column(String(64), nullable=False, default="bge-m3-v1")
    page_num = Column(Integer, nullable=True, comment="来源页码")
    chunk_index = Column(Integer, nullable=False, comment="分块序号")
    char_count = Column(Integer, nullable=False, comment="字符数")

    document = relationship("KbDocument", back_populates="chunks")

    def __repr__(self) -> str:
        return f"<KbChunk(id={self.id}, doc_id={self.document_id}, page={self.page_num})>"


class KbAtom(BaseModel):
    """知识库原子素材，用于组合资料的粒度化引用。"""

    __tablename__ = "kb_atoms"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "fingerprint",
            name="uq_kb_atoms_document_fingerprint",
        ),
    )

    document_id = Column(
        Integer,
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属知识资料ID",
    )
    title = Column(String(500), nullable=False, comment="原子素材标题")
    business_type = Column(String(64), nullable=False, index=True, comment="业务大类")
    business_label = Column(String(100), nullable=True, comment="业务细分标签")
    summary = Column(Text, nullable=True, comment="原子素材摘要")
    content = Column(Text, nullable=False, comment="原子素材文本快照")
    page_start = Column(Integer, nullable=True, comment="起始页")
    page_end = Column(Integer, nullable=True, comment="结束页")
    atom_type = Column(String(32), nullable=False, default="page", comment="原子类型")
    confidence = Column(Integer, nullable=False, default=80, comment="拆分置信度(0-100)")
    source_chunk_ids = Column(JSON, nullable=False, default=list, comment="来源 kb_chunk ID 列表")
    source_image_ids = Column(JSON, nullable=False, default=list, comment="来源图片 ID 列表")
    fingerprint = Column(String(64), nullable=False, index=True, comment="原子幂等指纹")
    processing_status = Column(String(20), nullable=False, default="completed", index=True)

    document = relationship("KbDocument", back_populates="atoms")
    chunks = relationship(
        "KbAtomChunk",
        back_populates="atom",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<KbAtom(id={self.id}, doc_id={self.document_id}, title='{self.title}')>"


class KbAtomChunk(BaseModel):
    """知识库原子素材分块。"""

    __tablename__ = "kb_atom_chunks"
    __table_args__ = (
        UniqueConstraint(
            "atom_id",
            "chunk_fingerprint",
            name="uq_kb_atom_chunks_atom_fingerprint",
        ),
    )

    atom_id = Column(
        Integer,
        ForeignKey("kb_atoms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属原子素材ID",
    )
    document_id = Column(
        Integer,
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="冗余所属知识资料ID",
    )
    content = Column(Text, nullable=False, comment="分块内容")
    embedding = Column(Vector(1024), nullable=True, comment="bge-m3 1024维向量")
    chunk_fingerprint = Column(String(64), nullable=False, index=True, comment="幂等指纹")
    embedding_version = Column(String(64), nullable=False, default="bge-m3-v1")
    page_num = Column(Integer, nullable=True, comment="来源页码")
    chunk_index = Column(Integer, nullable=False, comment="原子内分块序号")
    char_count = Column(Integer, nullable=False, comment="字符数")

    atom = relationship("KbAtom", back_populates="chunks")

    def __repr__(self) -> str:
        return f"<KbAtomChunk(id={self.id}, atom_id={self.atom_id}, index={self.chunk_index})>"
