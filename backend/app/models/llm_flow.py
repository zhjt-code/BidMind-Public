"""LLM Center -- 流程与阶段定义模型

llm_flows: 三大业务流程（投标审查/串标审查/标书编写）
llm_stages: 流程下的阶段分组（如招标提取、对比审查等）
"""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class LlmFlow(BaseModel):
    """LLM 流程定义（预置 Seed 数据）"""

    __tablename__ = "llm_flows"

    flow_key = Column(String(50), unique=True, nullable=False, comment="流程标识: bid_review / collusion / writing")
    name = Column(String(100), nullable=False, comment="流程名称")
    description = Column(Text, nullable=True, comment="流程描述")
    route_prefix = Column(String(50), nullable=True, comment="前端路由前缀")
    display_order = Column(Integer, default=0, nullable=False, comment="展示排序")
    is_enabled = Column(Boolean, default=True, nullable=False, comment="是否启用")

    stages = relationship(
        "LlmStage",
        back_populates="flow",
        cascade="all, delete-orphan",
        order_by="LlmStage.display_order",
    )

    def __repr__(self) -> str:
        return f"<LlmFlow(id={self.id}, flow_key='{self.flow_key}')>"


class LlmStage(BaseModel):
    """LLM 阶段定义（预置 Seed 数据）"""

    __tablename__ = "llm_stages"

    flow_id = Column(Integer, ForeignKey("llm_flows.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_key = Column(String(50), nullable=False, comment="阶段标识: tender_extract / review / ...")
    name = Column(String(100), nullable=False, comment="阶段名称")
    description = Column(Text, nullable=True, comment="阶段描述")
    display_order = Column(Integer, default=0, nullable=False, comment="展示排序")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")

    flow = relationship("LlmFlow", back_populates="stages")
    nodes = relationship(
        "LlmNode",
        back_populates="stage",
        cascade="all, delete-orphan",
        order_by="LlmNode.display_order",
    )

    __table_args__ = (
        UniqueConstraint("flow_id", "stage_key", name="uq_llm_stage_flow_key"),
    )

    def __repr__(self) -> str:
        return f"<LlmStage(id={self.id}, stage_key='{self.stage_key}')>"
