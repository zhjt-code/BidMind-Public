"""LLM Center -- 节点连接模型

只读 Seed 数据，供流程图渲染节点之间的数据流关系。
"""

from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import BaseModel


class LlmNodeEdge(BaseModel):
    """LLM 节点连接关系"""

    __tablename__ = "llm_node_edges"

    from_node_id = Column(Integer, ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    to_node_id = Column(Integer, ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    edge_type = Column(String(20), nullable=False, default="data_flow", comment="连接类型: data_flow / conditional / optional")
    param_mapping = Column(JSONB, nullable=True, comment='参数映射: {"from_output": "to_input"}')
    description = Column(Text, nullable=True, comment="连接说明")
    display_order = Column(Integer, default=0, nullable=False, comment="展示排序")

    __table_args__ = (
        UniqueConstraint("from_node_id", "to_node_id", name="uq_llm_edge_from_to"),
    )

    def __repr__(self) -> str:
        return f"<LlmNodeEdge(from={self.from_node_id}, to={self.to_node_id}, type='{self.edge_type}')>"
