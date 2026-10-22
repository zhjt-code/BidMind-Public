"""投标响应检索命中证据（Fix-02 / 问题 2 持久化）

持久化每条 BidResponse 的多路检索命中，作为下游 Gate（PageAnchorVerifyGate 等）
的事实参照源。消除 "审查 Gate 证据基座依赖 LLM 自产字段" 的结构性风险。

每条 BidResponse 可对应 N 条 retrieval chunks，来源角色（role）标识：
- primary：正文语义检索命中
- image：图片证据附件命中
- toc：目录导航命中
- deep：二次深搜命中
"""

from sqlalchemy import JSON, Column, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class BidResponseChunk(BaseModel):
    """投标响应的检索命中证据记录。

    一条 BidResponse 通过 ``bid_response_id`` 外键聚合多条命中记录，
    每条记录承载一次 retriever 命中的充分上下文（chunk_id / page_num /
    similarity / matched_sub_query / matched_keywords / role）。

    下游消费：
    - ``reviewer._prefetch_evidence``：装配 ``ReviewEvidenceBundle.retrieval_*``
    - ``PageAnchorVerifyGate``：用真实 chunk_pages 做 source_pages 交叉校验
    - ``_collect_chunk_pages_for_pair``：优先从本表取页集合，退化为文档全集
    """

    __tablename__ = "bid_response_chunks"

    bid_response_id = Column(
        Integer,
        ForeignKey("bid_responses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属投标响应 ID",
    )
    chunk_id = Column(
        Integer,
        ForeignKey("chunks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="来源分块 ID（chunk 被删除时置 NULL 不阻断）",
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="来源文档 ID（冗余便于批量查询）",
    )
    page_num = Column(
        Integer,
        nullable=True,
        comment="命中所在页码",
    )
    similarity = Column(
        Float,
        nullable=True,
        comment="检索相似度（0.0-1.0，TOC 命中为 0）",
    )
    matched_sub_query = Column(
        Text,
        nullable=True,
        comment="命中所属子查询文本（QueryDecomposer 分解后）",
    )
    matched_keywords = Column(
        JSON,
        nullable=True,
        comment="命中所用关键词列表（deep 搜索记录）",
    )
    role = Column(
        String(16),
        nullable=False,
        index=True,
        comment="命中来源角色：primary / image / toc / deep",
    )

    __table_args__ = (
        Index("ix_bid_response_chunks_doc_page", "document_id", "page_num"),
        Index("ix_bid_response_chunks_resp_role", "bid_response_id", "role"),
    )

    bid_response = relationship("BidResponse", back_populates="retrieval_chunks")

    def __repr__(self) -> str:
        return (
            f"<BidResponseChunk(id={self.id}, resp={self.bid_response_id}, "
            f"chunk={self.chunk_id}, page={self.page_num}, role='{self.role}')>"
        )
