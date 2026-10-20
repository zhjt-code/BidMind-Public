"""LLM 物理尝试事件模型 (P0-A 可靠性观测)

职责: 持久化每一次 LLM 物理请求(attempt)的事实记录, 作为可靠性指标的唯一分母来源
不做: 不做聚合计算、不承担 Prompt/输出内容存储(禁止记录完整文档内容)

与 llm_execution_traces 的区别:
- traces 按 (node_key, project_id) 聚合, 服务 LLM Center 展示与计费;
- 本表按物理 attempt 逐条记录, 服务运营总览的错误率/重试率/延迟分位数,
  跨进程(API/Worker)、跨重启可查, 按 traffic_class 隔离测试流量。
"""

from sqlalchemy import Column, DateTime, Index, Integer, SmallInteger, String
from sqlalchemy.sql import func

from app.models.base import BaseModel

# outcome 取值(单一真源: app/core/ai/llm_outcome.py 的 LlmOutcome 枚举):
# valid / empty / invalid_json / schema_invalid / http_error / transport_error
# / timeout / rate_limited / quota_exhausted / auth_error / degraded_plain_chat / cancelled

TRAFFIC_CLASS_PRODUCTION = "production"
TRAFFIC_CLASS_ADMIN_TEST = "admin_test"
TRAFFIC_CLASS_REGRESSION = "regression"


class LlmAttemptEvent(BaseModel):
    """单次 LLM 物理请求的事实记录"""

    __tablename__ = "llm_attempt_events"

    occurred_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="attempt 实际发生时间(非入库时间)",
    )
    logical_call_id = Column(
        String(36), nullable=False,
        comment="逻辑调用ID, 同一次业务调用的全部物理尝试共享",
    )
    attempt_no = Column(
        SmallInteger, nullable=False, default=1,
        comment="物理尝试序号, 1-based",
    )
    traffic_class = Column(
        String(16), nullable=False, default=TRAFFIC_CLASS_PRODUCTION,
        comment="流量类别: production / admin_test / regression",
    )
    process_role = Column(
        String(24), nullable=True,
        comment="进程角色: api / worker_llm / worker_process / worker_maintenance",
    )
    purpose = Column(String(16), nullable=True, comment="调度用途: review / vision / judge")
    provider = Column(String(24), nullable=True, comment="按 api_base 识别的 provider")
    model_name = Column(String(100), nullable=True, comment="实际模型名")
    config_id = Column(String(64), nullable=True, comment="模型配置ID(ai_settings)")
    node_key = Column(String(80), nullable=True, comment="LLM Center 节点标识")
    flow = Column(String(32), nullable=True, comment="业务流: bid_review / collusion / writing 等")
    project_id = Column(Integer, nullable=True, comment="关联项目")
    method = Column(
        String(24), nullable=True,
        comment="调用方法: chat / chat_json / chat_with_validation / chat_with_images / chat_cd",
    )
    http_status = Column(SmallInteger, nullable=True, comment="HTTP 状态码(有响应时)")
    outcome = Column(String(24), nullable=False, comment="结果分类, 见 LlmOutcome")
    rate_limit_kind = Column(
        String(20), nullable=True,
        comment="429 分类: rate_limit / quota_exhausted / auth_or_config / unknown_429",
    )
    queue_wait_ms = Column(Integer, nullable=True, comment="调度排队等待毫秒(仅首个 attempt 记录)")
    latency_ms = Column(Integer, nullable=False, default=0, comment="本次物理请求耗时毫秒")
    prompt_tokens = Column(Integer, nullable=False, default=0, comment="输入 token")
    completion_tokens = Column(Integer, nullable=False, default=0, comment="输出 token")

    __table_args__ = (
        Index("idx_llm_attempt_occurred", "occurred_at"),
        Index("idx_llm_attempt_traffic_time", "traffic_class", "occurred_at"),
        Index("idx_llm_attempt_purpose_time", "purpose", "occurred_at"),
        Index("idx_llm_attempt_logical", "logical_call_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<LlmAttemptEvent(id={self.id}, logical={self.logical_call_id}, "
            f"attempt={self.attempt_no}, outcome='{self.outcome}')>"
        )
