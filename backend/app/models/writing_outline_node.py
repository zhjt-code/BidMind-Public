"""标书编写 -- 目录树节点模型"""

import enum

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class OutlineNodeType(str, enum.Enum):
    """目录节点类型枚举

    决定内容生成路径:
    - fixed_template: 固定模板(纯算法填充)
    - data_fill: 资料填空(轻量AI)
    - free_writing: 自由论述(完整AI)
    """
    fixed_template = "fixed_template"
    data_fill = "data_fill"
    free_writing = "free_writing"


class OutlineNodeStatus(str, enum.Enum):
    """目录节点状态枚举"""
    pending = "pending"
    generating = "generating"
    draft = "draft"
    user_edited = "user_edited"
    finalized = "finalized"


class WritingOutlineNode(BaseModel):
    """目录树节点模型

    支持无限层级的树形目录结构，每个节点关联生成类型和状态。
    parent_id 为 NULL 表示顶级节点。
    """

    __tablename__ = "writing_outline_nodes"

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID",
    )
    parent_id = Column(
        Integer,
        ForeignKey("writing_outline_nodes.id", ondelete="CASCADE"),
        nullable=True,
        comment="父节点ID(NULL=顶级节点)",
    )
    title = Column(String(500), nullable=False, comment="章节标题")
    depth = Column(Integer, nullable=False, default=0, comment="嵌套深度(0=顶级)")
    sort_order = Column(Integer, nullable=False, default=0, comment="同级排序序号")
    node_type = Column(
        Enum(OutlineNodeType),
        nullable=False,
        comment="节点类型(决定生成路径)",
    )
    is_required = Column(
        Integer, default=1, nullable=False, comment="是否必填: 0/1"
    )
    score_weight = Column(Float, nullable=True, comment="评分权重")
    target_word_count = Column(Integer, nullable=True, comment="目标字数")
    status = Column(
        Enum(OutlineNodeStatus),
        default=OutlineNodeStatus.pending,
        nullable=False,
        comment="节点状态",
    )
    volume_type = Column(
        String(20),
        nullable=True,
        comment="所属分卷: technical/commercial",
    )
    is_skeleton = Column(
        Integer, default=0, nullable=False,
        comment="是否为骨架节点(招标文件指定,不可删除): 0/1",
    )
    embedding = Column(
        Vector(1024),
        nullable=True,
        comment="章节标题向量(用于语义匹配)",
    )
    chapter_profile = Column(
        Text,
        nullable=True,
        comment="章节画像JSON(semantic_role/expects/rejects)",
    )

    project = relationship("Project", back_populates="writing_outline_nodes")
    parent = relationship(
        "WritingOutlineNode",
        remote_side="WritingOutlineNode.id",
        back_populates="children",
    )
    children = relationship(
        "WritingOutlineNode",
        back_populates="parent",
        passive_deletes=True,
    )
    drafts = relationship(
        "WritingSectionDraft",
        back_populates="outline_node",
        passive_deletes=True,
    )
    section_plans = relationship(
        "WritingSectionPlan",
        back_populates="outline_node",
        passive_deletes=True,
    )
    bindings = relationship(
        "WritingBinding",
        back_populates="outline_node",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return (
            f"<WritingOutlineNode(id={self.id}, title='{self.title}', "
            f"depth={self.depth}, type='{self.node_type}')>"
        )
