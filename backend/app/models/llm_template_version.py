"""LLM Center -- Prompt 模板版本模型

受控发布生命周期: draft → validated → published → archived
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class LlmTemplateVersion(BaseModel):
    """Prompt 模板版本（受控发布）"""

    __tablename__ = "llm_template_versions"

    node_id = Column(Integer, ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    template_role = Column(String(10), nullable=False, default="system", comment="模板角色: system / user")
    version = Column(Integer, nullable=False, default=1, comment="版本号")
    content = Column(Text, nullable=False, comment="Prompt 模板文本（含 {变量} 占位符）")
    is_active = Column(Boolean, default=False, nullable=False, index=True, comment="是否为当前激活版本")
    source = Column(String(10), nullable=False, default="code", comment="来源: code / db")
    status = Column(String(20), nullable=False, default="draft", comment="状态: draft / validated / published / archived")
    change_note = Column(Text, nullable=True, comment="修改说明")
    created_by = Column(String(100), nullable=True, comment="修改人")
    validated_at = Column(DateTime(timezone=True), nullable=True, comment="校验通过时间")
    published_at = Column(DateTime(timezone=True), nullable=True, comment="发布时间")

    node = relationship("LlmNode", back_populates="template_versions")

    # 当前激活版本唯一性由迁移中的 partial unique index 保护:
    # uq_llm_tv_active_node_role (node_id, template_role) WHERE is_active = true
    __table_args__ = (
        UniqueConstraint("node_id", "template_role", "version", name="uq_llm_tv_node_role_version"),
    )

    def __repr__(self) -> str:
        return f"<LlmTemplateVersion(id={self.id}, node_id={self.node_id}, role='{self.template_role}', v={self.version})>"
