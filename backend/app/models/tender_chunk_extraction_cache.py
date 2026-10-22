"""招标要求单分块抽取缓存模型。"""

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import BaseModel


class TenderChunkExtractionCache(BaseModel):
    """缓存同一 chunk 在同一提取配置下的结构化抽取结果。"""

    __tablename__ = "tender_chunk_extraction_cache"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_fingerprint",
            "prompt_signature",
            "model_id",
            "extractor_schema_version",
            "section_context_enabled",
            name="uq_tender_chunk_extract_cache_key",
        ),
    )

    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属招标文档ID",
    )
    chunk_id = Column(
        Integer,
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="来源分块ID",
    )
    chunk_fingerprint = Column(
        String(64),
        nullable=False,
        index=True,
        comment="分块幂等指纹",
    )
    prompt_signature = Column(
        String(64),
        nullable=False,
        index=True,
        comment="system/user prompt 内容签名",
    )
    model_id = Column(String(200), nullable=False, comment="LLM模型标识")
    extractor_schema_version = Column(
        String(32),
        nullable=False,
        comment="抽取缓存结构版本",
    )
    section_context_enabled = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="缓存生成时是否启用章节上下文注入",
    )
    result_items = Column(
        JSONB,
        nullable=False,
        default=list,
        comment="ExtractedRequirement.model_dump() 列表",
    )
    empty_result = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="本 chunk 是否为合法空结果",
    )
    failure_fingerprint = Column(
        Text,
        nullable=True,
        comment="失败摘要指纹(观测用, 不代表可复用结果)",
    )
