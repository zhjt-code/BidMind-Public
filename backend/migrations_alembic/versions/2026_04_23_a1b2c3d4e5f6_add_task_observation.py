"""add_task_observation

Phase 2 监控体系: 新增统一任务观测表 task_observations, 用于跨三流程
(招标/围标/写作/解析) 归一化任务生命周期事件 (enqueue/start/stage/end).

Revision ID: a1b2c3d4e5f6
Revises: e8f9a0b1c2d3
Create Date: 2026-04-23 11:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a23ob1c4d5e6"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_observations",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False, comment="任务幂等标识"),
        sa.Column(
            "flow",
            sa.Enum(
                "tender", "collusion", "writer", "process", "other",
                name="taskflow",
            ),
            nullable=False,
            comment="业务 flow",
        ),
        sa.Column("task_type", sa.String(length=64), nullable=False, comment="任务子类型"),
        sa.Column(
            "phase",
            sa.Enum(
                "enqueue", "start", "stage", "end",
                name="taskphase",
            ),
            nullable=False,
            comment="事件 phase",
        ),
        sa.Column("stage", sa.String(length=64), nullable=True, comment="当前阶段名"),
        sa.Column("project_id", sa.Integer(), nullable=True, comment="关联项目 ID"),
        sa.Column(
            "outcome",
            sa.Enum(
                "success", "failure", "cancelled", "timeout", "degraded", "unknown",
                name="taskoutcome",
            ),
            nullable=True,
            comment="终态 outcome",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True, comment="持续毫秒"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_state", sa.String(length=32), nullable=True, comment="失败契约五态"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("degraded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("labels_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_observations_task_id", "task_observations", ["task_id"])
    op.create_index("ix_task_observations_flow", "task_observations", ["flow"])
    op.create_index("ix_task_observations_task_type", "task_observations", ["task_type"])
    op.create_index("ix_task_observations_phase", "task_observations", ["phase"])
    op.create_index("ix_task_observations_stage", "task_observations", ["stage"])
    op.create_index("ix_task_observations_project_id", "task_observations", ["project_id"])
    op.create_index("ix_task_observations_outcome", "task_observations", ["outcome"])
    op.create_index("ix_task_observations_failure_state", "task_observations", ["failure_state"])
    op.create_index(
        "ix_task_observations_flow_created_at",
        "task_observations",
        ["flow", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_observations_flow_created_at", table_name="task_observations")
    op.drop_index("ix_task_observations_failure_state", table_name="task_observations")
    op.drop_index("ix_task_observations_outcome", table_name="task_observations")
    op.drop_index("ix_task_observations_project_id", table_name="task_observations")
    op.drop_index("ix_task_observations_stage", table_name="task_observations")
    op.drop_index("ix_task_observations_phase", table_name="task_observations")
    op.drop_index("ix_task_observations_task_type", table_name="task_observations")
    op.drop_index("ix_task_observations_flow", table_name="task_observations")
    op.drop_index("ix_task_observations_task_id", table_name="task_observations")
    op.drop_table("task_observations")
    sa.Enum(name="taskoutcome").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="taskphase").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="taskflow").drop(op.get_bind(), checkfirst=True)
