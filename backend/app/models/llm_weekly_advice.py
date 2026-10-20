"""
职责: 持久化周度 LLM 调度建议与应用状态
不做: 不做规则计算、不写回 priority、不承担审计写入
"""

from sqlalchemy import Column, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import BaseModel


class LlmWeeklyAdvice(BaseModel):
    """周度模型调度建议（算法产出，人工确认后可写回）。"""

    __tablename__ = "llm_weekly_advice"

    week_start = Column(
        DateTime(timezone=True), nullable=False,
        comment="周起始(周一 00:00 Asia/Shanghai 对应的瞬时)",
    )
    scope = Column(
        String(16), nullable=False,
        comment="建议作用域: purpose / node",
    )
    target = Column(
        String(80), nullable=False,
        comment="目标标识: purpose 名或 node_key",
    )
    advice_action = Column(
        String(32), nullable=False,
        comment="promote / demote / keep / insufficient_data",
    )
    recommended_config_id = Column(
        String(64), nullable=True,
        comment="建议提升为主用的 config_id",
    )
    demote_config_id = Column(
        String(64), nullable=True,
        comment="建议降权的 config_id",
    )
    proposed_priority = Column(
        JSONB, nullable=True,
        comment="建议写回的完整 priority map",
    )
    evidence_json = Column(
        JSONB, nullable=False, default=dict,
        comment="证据数字与候选列表",
    )
    rule_id = Column(
        String(64), nullable=False, default="",
        comment="命中的规则 ID",
    )
    status = Column(
        String(24), nullable=False, default="pending",
        comment="pending / applied / rolled_back / dismissed",
    )
    applied_snapshot_id = Column(
        Integer, nullable=True,
        comment="应用时拍摄的 priority 快照 ID",
    )
    applied_at = Column(DateTime(timezone=True), nullable=True)
    applied_by = Column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_llm_weekly_advice_week", "week_start"),
        Index("idx_llm_weekly_advice_status", "status"),
        Index(
            "uq_llm_weekly_advice_week_scope_target",
            "week_start", "scope", "target",
            unique=True,
        ),
    )
