"""投标文件材料事实库存

项目级材料存在性登记: 联合身份 = document_id + chunk_id + physical_page,
按规范化材料键单调并集, 不承担符合性判断。
"""

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class MaterialFact(BaseModel):
    """文档级材料事实行。"""

    __tablename__ = "material_facts"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "canonical_material_key",
            "document_id",
            "chunk_id",
            "physical_page",
            name="uq_material_facts_identity",
        ),
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID",
    )
    canonical_material_key = Column(
        String(255),
        nullable=False,
        index=True,
        comment="规范化材料键",
    )
    raw_noun = Column(String(255), nullable=False, comment="原始材料名词")
    grade = Column(String(16), nullable=False, comment="material / mention")
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="来源投标文档ID",
    )
    bid_part = Column(
        String(16),
        nullable=True,
        comment="信封卷角色: NULL=整本 / commercial / technical",
    )
    chunk_id = Column(
        Integer,
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="来源分块ID",
    )
    physical_page = Column(
        Integer,
        nullable=False,
        default=0,
        comment="PDF物理页; 未知为0",
    )
    identifier = Column(String(128), nullable=True, comment="证号/编号")
    subject = Column(String(255), nullable=True, comment="主体(归一化机构名)")
    source_channel = Column(
        String(32),
        nullable=False,
        comment="lexical / toc / ocr / retrieval_bag / review_search",
    )
    excerpt = Column(Text, nullable=True, comment="证据摘录")

    project = relationship("Project")
    document = relationship("Document")
    chunk = relationship("Chunk")
