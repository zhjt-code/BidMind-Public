"""标书编写 -- 章节论证计划模型"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class WritingSectionPlan(BaseModel):
    """章节论证计划版本。

    计划先于正文生成，用于绑定输入指纹、Prompt快照、论点和证据引用。
    """

    __tablename__ = "writing_section_plans"

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
    version = Column(Integer, nullable=False, comment="同一节点的计划版本号")
    schema_version = Column(
        String(40),
        nullable=False,
        default="section_plan.v1",
        comment="SectionPlan契约版本",
    )
    input_fingerprint = Column(
        String(64),
        nullable=False,
        comment="规划输入稳定指纹",
    )
    input_manifest = Column(
        JSONB,
        nullable=False,
        comment="规划输入清单(仅ID/哈希/版本,不含原文)",
    )
    prompt_snapshot = Column(
        JSONB,
        nullable=False,
        comment="planner/free_writing Prompt版本和哈希快照",
    )
    plan_payload = Column(
        JSONB,
        nullable=True,
        comment="已校验的SectionPlan payload; failed时为空",
    )
    status = Column(
        String(20),
        nullable=False,
        default="planning",
        comment="planning/ready/failed",
    )
    is_current = Column(
        Integer,
        nullable=False,
        default=0,
        comment="是否为当前ready计划: 0/1",
    )
    attempt_count = Column(Integer, nullable=False, default=0, comment="规划尝试次数")
    attempt_trace = Column(JSONB, nullable=True, comment="失败/接管摘要,不含原始LLM输出")
    generation_token = Column(String(64), nullable=True, comment="租约接管token")
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, comment="规划租约过期时间")
    failure_code = Column(String(80), nullable=True, comment="失败码")
    failure_reason = Column(Text, nullable=True, comment="失败摘要")
    completed_at = Column(DateTime(timezone=True), nullable=True, comment="规划完成时间")

    outline_node = relationship("WritingOutlineNode", back_populates="section_plans")
    drafts = relationship("WritingSectionDraft", back_populates="section_plan")

    __table_args__ = (
        UniqueConstraint(
            "outline_node_id",
            "version",
            name="uq_writing_section_plans_node_version",
        ),
        CheckConstraint(
            "status in ('planning', 'ready', 'failed')",
            name="ck_writing_section_plans_status",
        ),
        CheckConstraint(
            "is_current in (0, 1)",
            name="ck_writing_section_plans_is_current",
        ),
        Index(
            "uq_writing_section_plans_current_ready",
            "outline_node_id",
            unique=True,
            postgresql_where=text("status = 'ready' AND is_current = 1"),
        ),
        Index(
            "uq_writing_section_plans_ready_fingerprint",
            "outline_node_id",
            "input_fingerprint",
            unique=True,
            postgresql_where=text("status = 'ready'"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WritingSectionPlan(id={self.id}, node_id={self.outline_node_id}, "
            f"version={self.version}, status='{self.status}')>"
        )
