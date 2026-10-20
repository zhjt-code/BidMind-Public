"""
职责: 持久化调度 priority 快照, 支撑写回审计与回滚
不做: 不直接修改 ai_settings、不做规则计算
"""

from sqlalchemy import Column, Integer, String, Index
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import BaseModel


class LlmPrioritySnapshot(BaseModel):
    """用途池 priority 快照。"""

    __tablename__ = "llm_priority_snapshots"

    purpose = Column(String(16), nullable=False, comment="vision/review/judge/extract")
    priority_json = Column(
        JSONB, nullable=False,
        comment="完整 {config_id: priority} 映射",
    )
    fingerprint = Column(
        String(64), nullable=False,
        comment="priority_json 的稳定指纹, 用于并发冲突检测",
    )
    reason = Column(
        String(64), nullable=False, default="advice_apply",
        comment="快照原因: advice_apply / manual / rollback",
    )
    advice_id = Column(Integer, nullable=True, comment="关联的周建议 ID")
    created_by = Column(String(64), nullable=True, comment="操作者用户名")

    __table_args__ = (
        Index("idx_llm_priority_snapshots_purpose", "purpose"),
        Index("idx_llm_priority_snapshots_advice", "advice_id"),
    )
