"""add_task_runs

为 docs/architecture.md 第五章显式重试契约新增 task_runs 表。
该表记录节点级 attempt、操作者、最后阶段和 reset/preserved 审计摘要；
task_observations 继续作为事件流，不承担当前运行状态职责。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b2f7d8e9a0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False, comment="所属项目ID"),
        sa.Column("flow", sa.String(length=64), nullable=False, comment="业务流"),
        sa.Column("node_key", sa.String(length=128), nullable=False, comment="节点标识"),
        sa.Column("task_name", sa.String(length=128), nullable=False, comment="后台任务名"),
        sa.Column("status", sa.String(length=32), nullable=False, comment="运行状态"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1", comment="节点运行轮次"),
        sa.Column("current_arq_job_id", sa.String(length=128), nullable=True, comment="当前 ARQ job id"),
        sa.Column("last_stage", sa.String(length=64), nullable=True, comment="最近阶段"),
        sa.Column("last_error", sa.Text(), nullable=True, comment="最近错误摘要"),
        sa.Column("retry_requested_by", sa.Integer(), nullable=True, comment="触发操作者ID"),
        sa.Column("retry_requested_at", sa.DateTime(timezone=True), nullable=True, comment="触发时间"),
        sa.Column("preserved_summary_json", sa.Text(), nullable=True, comment="保留产物摘要 JSON"),
        sa.Column("reset_scope_json", sa.Text(), nullable=True, comment="重置范围摘要 JSON"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["retry_requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "flow",
            "node_key",
            "attempt",
            name="uq_task_runs_project_flow_node_attempt",
        ),
    )
    op.create_index("ix_task_runs_project_id", "task_runs", ["project_id"])
    op.create_index("ix_task_runs_flow", "task_runs", ["flow"])
    op.create_index("ix_task_runs_node_key", "task_runs", ["node_key"])
    op.create_index("ix_task_runs_task_name", "task_runs", ["task_name"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])
    op.create_index("ix_task_runs_retry_requested_by", "task_runs", ["retry_requested_by"])
    op.create_index(
        "ix_task_runs_project_flow_node_status",
        "task_runs",
        ["project_id", "flow", "node_key", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_runs_project_flow_node_status", table_name="task_runs")
    op.drop_index("ix_task_runs_retry_requested_by", table_name="task_runs")
    op.drop_index("ix_task_runs_status", table_name="task_runs")
    op.drop_index("ix_task_runs_task_name", table_name="task_runs")
    op.drop_index("ix_task_runs_node_key", table_name="task_runs")
    op.drop_index("ix_task_runs_flow", table_name="task_runs")
    op.drop_index("ix_task_runs_project_id", table_name="task_runs")
    op.drop_table("task_runs")
