"""标书编写 -- 素材分块模型(含向量)"""

from sqlalchemy import Column, Integer, ForeignKey, Text, String, UniqueConstraint
from sqlalchemy.orm import relationship, backref
from pgvector.sqlalchemy import Vector
from app.models.base import BaseModel


class WritingMaterialChunk(BaseModel):
    """素材分块模型:存储素材文档分块及其向量表示

    独立于投标审查的 Chunk 表,专用于标书编写场景。
    通过 material_id 关联 WritingMaterial 表,支持级联删除。

    Attributes:
        material_id: 所属素材ID
        project_id: 所属项目ID(冗余字段,便于按项目聚合查询)
        content: 分块文本内容
        embedding: 1024维向量(bge-m3模型)
        chunk_index: 分块序号(从0开始)
        char_count: 字符数
        page_num: 来源页码(可为空)
    """

    __tablename__ = "writing_material_chunks"
    __table_args__ = (
        UniqueConstraint(
            "material_id",
            "chunk_fingerprint",
            name="uq_writing_material_chunks_material_fingerprint",
        ),
    )

    material_id = Column(
        Integer,
        ForeignKey("writing_materials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属素材ID",
    )
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID",
    )
    content = Column(Text, nullable=False, comment="分块内容")
    embedding = Column(
        Vector(1024),
        nullable=True,
        comment="文本向量(用于语义匹配)",
    )
    chunk_fingerprint = Column(String(64), nullable=False, index=True, comment="幂等指纹")
    embedding_version = Column(String(64), nullable=False, default="bge-m3-v1", comment="向量版本")
    parent_block_id = Column(String(128), nullable=True, comment="父级语义块ID")
    section_path = Column(Text, nullable=True, comment="章节/条款路径")
    chunk_index = Column(
        Integer,
        nullable=False,
        comment="分块序号(从0开始)",
    )
    char_count = Column(Integer, nullable=False, comment="字符数")
    page_num = Column(Integer, nullable=True, comment="来源页码")

    material = relationship(
        "WritingMaterial",
        backref=backref("chunks", passive_deletes=True),
        foreign_keys=[material_id],
    )

    def __repr__(self) -> str:
        return (
            f"<WritingMaterialChunk(id={self.id}, material_id={self.material_id}, "
            f"index={self.chunk_index}, chars={self.char_count})>"
        )
