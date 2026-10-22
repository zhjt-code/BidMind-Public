"""标书编写 -- 章节生成图示模型

职责: 记录 SectionPlan 图示意图物化后的 Diagram IR、PNG 资产与状态。
不做: 不存储素材上传图片(见 WritingMaterialImage)、不承载渲染布局逻辑。
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class WritingSectionDiagram(BaseModel):
    """章节事实型生成图示。

    每条记录对应 SectionPlan 中一个通过闸门的 VisualIntent，
    经确定性 IR 编译与 PNG 渲染后由正文 figure 协议消费。
    """

    __tablename__ = "writing_section_diagrams"

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属写标项目ID",
    )
    outline_node_id = Column(
        Integer,
        ForeignKey("writing_outline_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属目录节点ID",
    )
    section_plan_id = Column(
        Integer,
        ForeignKey("writing_section_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="来源章节论证计划ID",
    )
    visual_id = Column(
        String(64),
        nullable=False,
        comment="SectionPlan 中的 VisualIntent 标识",
    )
    diagram_type = Column(
        String(20),
        nullable=False,
        comment="flowchart/gantt/swimlane/roadmap/org_chart/layered/network/wbs/zone_layout",
    )
    title = Column(String(200), nullable=False, comment="图题(进入figcaption)")
    ir_json = Column(JSONB, nullable=True, comment="Diagram IR; 编译失败时为空")
    png_path = Column(String(500), nullable=True, comment="渲染PNG文件路径")
    content_sha256 = Column(
        String(64),
        nullable=True,
        comment="IR内容哈希, 用于幂等复用",
    )
    status = Column(
        String(20),
        nullable=False,
        default="pending",
        comment="pending/ready/failed",
    )
    failure_code = Column(String(80), nullable=True, comment="编译/渲染失败码")

    section_plan = relationship("WritingSectionPlan", backref="diagrams")

    __table_args__ = (
        UniqueConstraint(
            "section_plan_id",
            "visual_id",
            name="uq_writing_section_diagrams_plan_visual",
        ),
        CheckConstraint(
            "status in ('pending', 'ready', 'failed')",
            name="ck_writing_section_diagrams_status",
        ),
        CheckConstraint(
            "diagram_type in ('flowchart', 'gantt', 'swimlane', "
            "'roadmap', 'org_chart', 'layered', 'network', 'wbs', 'zone_layout')",
            name="ck_writing_section_diagrams_type",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WritingSectionDiagram(id={self.id}, plan_id={self.section_plan_id}, "
            f"visual_id='{self.visual_id}', status='{self.status}')>"
        )
