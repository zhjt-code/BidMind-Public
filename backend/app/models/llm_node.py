"""LLM Center -- 节点定义模型

每个 LLM 调用点对应一个节点，记录其元数据、执行配置和管理属性。
"""

from sqlalchemy import Column, String, Text, Integer, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class LlmNode(BaseModel):
    """LLM 节点定义（核心：每个 LLM 调用点）"""

    __tablename__ = "llm_nodes"

    stage_id = Column(Integer, ForeignKey("llm_stages.id", ondelete="CASCADE"), nullable=False, index=True)
    node_key = Column(String(80), unique=True, nullable=False, comment="节点唯一标识: bid_review.tender_extract_page")
    name = Column(String(100), nullable=False, comment="节点名称")
    description = Column(Text, nullable=True, comment="节点功能说明")

    # 工厂函数注册
    builder_key = Column(String(200), nullable=True, comment="工厂函数标识: review.build_review_prompt")
    resolver_key = Column(String(200), nullable=True, comment="参数解析器: review.evidence_bundle_resolver")
    source_file = Column(String(200), nullable=True, comment="代码来源: core/ai/prompts/tender_extract.py")

    # 执行层桥接
    execution_channel = Column(String(20), nullable=False, default="review", comment="执行通道: review / vision")
    call_method = Column(String(30), nullable=False, default="chat_json", comment="调用方式: chat / chat_json / chat_with_validation / chat_with_images")

    # 运行时默认配置
    default_temperature = Column(Float, default=0.1, nullable=False, comment="默认采样温度")
    default_max_tokens = Column(Integer, default=2000, nullable=False, comment="默认最大输出 token")

    # 输出契约
    output_schema_name = Column(String(100), nullable=True, comment="Pydantic 模型名: ExtractedReviewResult")

    # 管理属性
    visibility = Column(String(20), nullable=False, default="main", comment="可见性: main / technical")
    editable_level = Column(String(20), nullable=False, default="advanced", comment="编辑权限: readonly / advanced / admin_only")
    supports_hot_reload = Column(Boolean, default=True, nullable=False, comment="是否支持热更新")
    display_order = Column(Integer, default=0, nullable=False, comment="展示排序")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")
    node_class = Column(
        String(16),
        nullable=False,
        server_default="judgment",
        comment="节点分类: mechanical / judgment",
    )

    # 关系
    stage = relationship("LlmStage", back_populates="nodes")
    template_versions = relationship(
        "LlmTemplateVersion",
        back_populates="node",
        cascade="all, delete-orphan",
        order_by="LlmTemplateVersion.version.desc()",
    )
    param_defs = relationship(
        "LlmParamDef",
        back_populates="node",
        cascade="all, delete-orphan",
        order_by="LlmParamDef.display_order",
    )

    def __repr__(self) -> str:
        return f"<LlmNode(id={self.id}, node_key='{self.node_key}')>"
