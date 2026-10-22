"""LLM Center -- 发布记录模型

审计追踪：记录所有 Prompt 版本的发布和回滚操作。
"""

from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import BaseModel


class LlmPublishRecord(BaseModel):
    """LLM 发布记录（审计追踪）"""

    __tablename__ = "llm_publish_records"

    node_id = Column(Integer, ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    template_version_id = Column(Integer, ForeignKey("llm_template_versions.id"), nullable=False)
    action = Column(String(20), nullable=False, comment="操作: publish / rollback")
    published_by = Column(String(100), nullable=False, comment="操作人")
    publish_scope = Column(String(50), nullable=True, comment="影响范围描述")
    previous_version_id = Column(Integer, ForeignKey("llm_template_versions.id"), nullable=True, comment="回滚点")
    validation_result = Column(JSONB, nullable=True, comment="校验结果快照")
    change_note = Column(Text, nullable=True, comment="变更说明")

    def __repr__(self) -> str:
        return f"<LlmPublishRecord(id={self.id}, action='{self.action}', node_id={self.node_id})>"
