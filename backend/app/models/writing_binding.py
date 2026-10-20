"""标书编写 -- 三元绑定模型"""

import enum
from sqlalchemy import Column, String, Integer, Float, ForeignKey, Enum
from sqlalchemy.orm import relationship, backref
from app.models.base import BaseModel


class BindingType(str, enum.Enum):
    """绑定类型枚举

    三元图中的三种边:
    - clause_to_node: 约束 -> 章节
    - material_to_node: 素材 -> 章节
    - material_to_clause: 素材 -> 约束
    """
    clause_to_node = "clause_to_node"
    material_to_node = "material_to_node"
    material_to_clause = "material_to_clause"


class BindingStatus(str, enum.Enum):
    """绑定状态枚举"""
    suggested = "suggested"
    confirmed = "confirmed"
    rejected = "rejected"


class WritingBinding(BaseModel):
    """三元绑定模型

    存储约束-章节-素材之间的关联关系，构成三元图。
    每条绑定记录代表图中的一条边，通过 binding_type 区分边类型。
    """

    __tablename__ = "writing_bindings"

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID",
    )
    outline_node_id = Column(
        Integer,
        ForeignKey("writing_outline_nodes.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联目录节点ID",
    )
    clause_id = Column(
        Integer,
        ForeignKey("writing_clauses.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联约束ID",
    )
    material_id = Column(
        Integer,
        ForeignKey("writing_materials.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联素材ID",
    )
    binding_type = Column(
        Enum(BindingType),
        nullable=False,
        comment="绑定类型",
    )
    confidence = Column(
        Float, default=0.0, nullable=False, comment="置信度(0.0~1.0)"
    )
    source = Column(
        String(10),
        default="auto",
        nullable=False,
        comment="来源: auto(AI推荐) / manual(人工)",
    )
    status = Column(
        Enum(BindingStatus),
        default=BindingStatus.suggested,
        nullable=False,
        comment="绑定状态",
    )
    reason = Column(
        String(300),
        nullable=True,
        comment="AI匹配理由",
    )
    scope = Column(
        String(20),
        nullable=True,
        comment="约束作用域: global/exclusive/shared/orphan",
    )

    project = relationship("Project", back_populates="writing_bindings")
    outline_node = relationship(
        "WritingOutlineNode",
        back_populates="bindings",
    )
    clause = relationship("WritingClause", backref="bindings")
    material = relationship(
        "WritingMaterial",
        backref=backref("bindings", passive_deletes=True),
    )

    def __repr__(self) -> str:
        return (
            f"<WritingBinding(id={self.id}, type='{self.binding_type}', "
            f"confidence={self.confidence})>"
        )
