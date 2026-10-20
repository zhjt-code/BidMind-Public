"""LLM Center -- 执行轨迹模型

可观测性：记录 LLM 调用的聚合统计和调试明细。

trace_mode:
- aggregate: 每个 (project_id, node_key) 一行聚合数据，定期归档
- full: 完整输入输出快照，仅调试模式启用
"""

from sqlalchemy import Column, String, Text, Integer, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import BaseModel


class LlmExecutionTrace(BaseModel):
    """LLM 执行轨迹"""

    __tablename__ = "llm_execution_traces"

    project_id = Column(Integer, nullable=True, index=True, comment="关联项目")
    node_key = Column(String(80), nullable=False, comment="节点标识")
    template_version_id = Column(Integer, nullable=True, comment="使用的模板版本")
    execution_channel = Column(String(20), nullable=True, comment="实际执行通道")
    model_name = Column(String(100), nullable=True, comment="实际模型名")
    temperature = Column(Float, nullable=True, comment="实际温度")
    max_tokens = Column(Integer, nullable=True, comment="实际 max_tokens")
    trace_mode = Column(String(10), nullable=False, default="aggregate", comment="追踪模式: aggregate / full")

    # aggregate 模式字段
    call_count = Column(Integer, default=1, nullable=False, comment="调用次数")
    total_latency_ms = Column(Float, default=0, nullable=False, comment="累计延迟(ms)")
    total_prompt_tokens = Column(Integer, default=0, nullable=False, comment="累计 prompt token")
    total_completion_tokens = Column(Integer, default=0, nullable=False, comment="累计 completion token")
    error_count = Column(Integer, default=0, nullable=False, comment="错误次数")
    retry_count = Column(Integer, default=0, nullable=False, comment="重试次数")

    # full 模式字段
    input_snapshot = Column(JSONB, nullable=True, comment="输入参数快照")
    output_summary = Column(Text, nullable=True, comment="输出摘要")
    raw_output = Column(Text, nullable=True, comment="原始输出（仅调试模式）")
    fallback_info = Column(JSONB, nullable=True, comment="熔断/降级信息")

    __table_args__ = (
        Index("idx_exec_traces_node_project", "node_key", "project_id"),
        Index("idx_exec_traces_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<LlmExecutionTrace(id={self.id}, node_key='{self.node_key}', mode='{self.trace_mode}')>"
