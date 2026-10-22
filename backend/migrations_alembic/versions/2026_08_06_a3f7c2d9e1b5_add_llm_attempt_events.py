"""add_llm_attempt_events

P0-A 可靠性观测: 新增 llm_attempt_events 事实表, 逐条记录 LLM 物理请求
(attempt), 提供跨进程、跨重启、可隔离测试流量的可靠性指标分母。

保留策略: 由 log_retention_service 按 30 天 TTL 分批清理。

Revision ID: a3f7c2d9e1b5
Revises: b5e2f9c47a10
Create Date: 2026-08-06 16:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3f7c2d9e1b5"
down_revision: Union[str, None] = "b5e2f9c47a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_attempt_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
            comment="attempt 实际发生时间(非入库时间)",
        ),
        sa.Column(
            "logical_call_id", sa.String(length=36), nullable=False,
            comment="逻辑调用ID, 同一次业务调用的全部物理尝试共享",
        ),
        sa.Column(
            "attempt_no", sa.SmallInteger(), nullable=False,
            comment="物理尝试序号, 1-based",
        ),
        sa.Column(
            "traffic_class", sa.String(length=16), nullable=False,
            comment="流量类别: production / admin_test / regression",
        ),
        sa.Column(
            "process_role", sa.String(length=24), nullable=True,
            comment="进程角色: api / worker_llm / worker_process / worker_maintenance",
        ),
        sa.Column("purpose", sa.String(length=16), nullable=True, comment="调度用途: review / vision / judge"),
        sa.Column("provider", sa.String(length=24), nullable=True, comment="按 api_base 识别的 provider"),
        sa.Column("model_name", sa.String(length=100), nullable=True, comment="实际模型名"),
        sa.Column("config_id", sa.String(length=64), nullable=True, comment="模型配置ID(ai_settings)"),
        sa.Column("node_key", sa.String(length=80), nullable=True, comment="LLM Center 节点标识"),
        sa.Column("flow", sa.String(length=32), nullable=True, comment="业务流: bid_review / collusion / writing 等"),
        sa.Column("project_id", sa.Integer(), nullable=True, comment="关联项目"),
        sa.Column(
            "method", sa.String(length=24), nullable=True,
            comment="调用方法: chat / chat_json / chat_with_validation / chat_with_images / chat_cd",
        ),
        sa.Column("http_status", sa.SmallInteger(), nullable=True, comment="HTTP 状态码(有响应时)"),
        sa.Column("outcome", sa.String(length=24), nullable=False, comment="结果分类, 见 LlmOutcome"),
        sa.Column(
            "rate_limit_kind", sa.String(length=20), nullable=True,
            comment="429 分类: rate_limit / quota_exhausted / auth_or_config / unknown_429",
        ),
        sa.Column("queue_wait_ms", sa.Integer(), nullable=True, comment="调度排队等待毫秒(仅首个 attempt 记录)"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, comment="本次物理请求耗时毫秒"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, comment="输入 token"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, comment="输出 token"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_attempt_events_id", "llm_attempt_events", ["id"])
    op.create_index("idx_llm_attempt_occurred", "llm_attempt_events", ["occurred_at"])
    op.create_index(
        "idx_llm_attempt_traffic_time", "llm_attempt_events",
        ["traffic_class", "occurred_at"],
    )
    op.create_index(
        "idx_llm_attempt_purpose_time", "llm_attempt_events",
        ["purpose", "occurred_at"],
    )
    op.create_index("idx_llm_attempt_logical", "llm_attempt_events", ["logical_call_id"])


def downgrade() -> None:
    op.drop_index("idx_llm_attempt_logical", table_name="llm_attempt_events")
    op.drop_index("idx_llm_attempt_purpose_time", table_name="llm_attempt_events")
    op.drop_index("idx_llm_attempt_traffic_time", table_name="llm_attempt_events")
    op.drop_index("idx_llm_attempt_occurred", table_name="llm_attempt_events")
    op.drop_index("ix_llm_attempt_events_id", table_name="llm_attempt_events")
    op.drop_table("llm_attempt_events")
