"""标书编写 -- 章节草稿模型"""

import enum
from sqlalchemy import Column, DateTime, Float, SmallInteger, String, Integer, Text, ForeignKey, Enum, JSON
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class DraftStatus(str, enum.Enum):
    """草稿状态枚举"""
    generating = "generating"
    draft = "draft"
    reviewed = "reviewed"
    finalized = "finalized"


class WritingSectionDraft(BaseModel):
    """章节草稿模型

    存储每个目录节点的内容草稿，支持多版本。
    is_current=1 表示当前使用的版本。
    source_clause_ids 和 source_material_ids 以 JSON 字符串形式存储。
    """

    __tablename__ = "writing_section_drafts"

    outline_node_id = Column(
        Integer,
        ForeignKey("writing_outline_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联目录节点ID",
    )
    version = Column(
        Integer, default=1, nullable=False, comment="版本号"
    )
    section_plan_id = Column(
        Integer,
        ForeignKey("writing_section_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="实际使用的章节论证计划ID",
    )
    planning_mode = Column(
        String(32), nullable=True, comment="planned_new/planned_reused/legacy_*",
    )
    planning_failure_code = Column(
        String(80), nullable=True, comment="规划降级失败码",
    )
    content = Column(Text, nullable=True, comment="草稿内容")
    content_format = Column(
        String(20), default="html", nullable=False, comment="内容格式: html/markdown"
    )
    summary = Column(
        String(1000), nullable=True, comment="章节摘要(供后续章节参考)"
    )
    source_clause_ids = Column(
        Text, nullable=True, comment="引用的约束ID列表(JSON)"
    )
    source_material_ids = Column(
        Text, nullable=True, comment="引用的素材ID列表(JSON)"
    )
    is_current = Column(
        Integer, default=1, nullable=False, comment="是否为当前版本: 0/1"
    )
    is_user_edited = Column(
        Integer, default=0, nullable=False, comment="是否经用户编辑: 0/1"
    )
    is_locked = Column(
        Integer, default=0, nullable=False, comment="是否已锁定: 0/1"
    )
    word_count = Column(Integer, nullable=True, comment="字数统计")
    status = Column(
        Enum(DraftStatus),
        default=DraftStatus.generating,
        nullable=False,
        comment="草稿状态",
    )

    # B1: 失败合同字段（failure_contract §4.1）
    failure_state = Column(String(32), nullable=False, server_default="completed", comment="五状态枚举")
    failure_reason = Column(Text, nullable=True, comment="人类可读失败原因")
    confidence = Column(Float, nullable=True, comment="Gate 综合置信度 0.0-1.0")
    gate_trace = Column(JSON, nullable=True, comment="每道 Gate verdict 列表")
    review_required = Column(SmallInteger, nullable=False, server_default="0", comment="是否需人工复核")
    reviewed_at = Column(DateTime(timezone=True), nullable=True, comment="人工复核完成时间")
    reviewed_by = Column(Integer, nullable=True, comment="复核人 user_id")

    # P2.1a: Gate verdict 压缩成 flags 字典, 供前端章节级橙标展示;
    # 示例: {"material_trace_missing": true, "table_fill_rate": 0.42, "ai_phrase_count": 15}
    gate_flags = Column(JSON, nullable=True, comment="单章级 Gate 信号 (素材未引用 / 表格填充率 / AI 套话等)")

    outline_node = relationship(
        "WritingOutlineNode",
        back_populates="drafts",
    )
    section_plan = relationship(
        "WritingSectionPlan",
        back_populates="drafts",
    )

    def __repr__(self) -> str:
        return (
            f"<WritingSectionDraft(id={self.id}, node_id={self.outline_node_id}, "
            f"version={self.version}, status='{self.status}')>"
        )
