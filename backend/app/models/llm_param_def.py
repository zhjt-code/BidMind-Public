"""LLM Center -- 参数定义模型

描述每个 LLM 节点的输入/输出/运行时参数契约，供前端 schema-driven 渲染。
"""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class LlmParamDef(BaseModel):
    """LLM 节点参数定义"""

    __tablename__ = "llm_param_defs"

    node_id = Column(Integer, ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    param_key = Column(String(80), nullable=False, comment="参数键名: content / category / requirements")
    direction = Column(String(10), nullable=False, comment="方向: input / output / runtime")
    data_type = Column(String(30), nullable=False, default="string", comment="数据类型: string / int / float / bool / json / array")
    is_required = Column(Boolean, default=True, nullable=False, comment="是否必填")
    default_value = Column(Text, nullable=True, comment="默认值（JSON 格式）")
    description = Column(Text, nullable=True, comment="参数说明")
    source_type = Column(String(20), default="manual", nullable=False, comment="来源类型: upstream / manual / system / derived")
    source_hint = Column(String(200), nullable=True, comment="来源提示")
    example_value = Column(Text, nullable=True, comment="示例值")
    ui_hint = Column(String(20), default="tooltip", nullable=False, comment="UI 展示: tooltip / tag / hidden")
    status = Column(String(20), nullable=False, default="active", comment="状态: active / deprecated / removed")
    display_order = Column(Integer, default=0, nullable=False, comment="展示排序")

    node = relationship("LlmNode", back_populates="param_defs")

    __table_args__ = (
        UniqueConstraint("node_id", "direction", "param_key", name="uq_llm_pd_node_dir_key"),
    )

    def __repr__(self) -> str:
        return f"<LlmParamDef(id={self.id}, param_key='{self.param_key}', direction='{self.direction}')>"
