"""expand vector job state machine

将 vector_job_states 状态机从 pending/running/completed/failed
扩展为 pending/queued/claimed/running/completed/failed/lost/recovering,
并新增 ARQ 运行态字段: arq_job_id / worker_id / enqueued_at / claimed_at /
started_at / finished_at / last_heartbeat_at。

Revision ID: 6d1c9f2b3a4e
Revises: 5c7d8e9f0a1b
Create Date: 2026-04-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6d1c9f2b3a4e"
down_revision: Union[str, None] = "5c7d8e9f0a1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# PG 枚举 ALTER 必须在事务外执行
_NEW_ENUM_VALUES = ("queued", "claimed", "lost", "recovering")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # PG 12+ 允许 ALTER TYPE ADD VALUE 在事务内执行, 但不能参数化
        # 枚举值白名单硬编码, 杜绝 SQL 注入
        for value in _NEW_ENUM_VALUES:
            if value not in {"queued", "claimed", "lost", "recovering"}:
                continue
            op.execute(f"ALTER TYPE vectorjobstatus ADD VALUE IF NOT EXISTS '{value}'")

    # 新增运行态字段, 全部可空 (历史行不回填, 由新路径写入)
    op.add_column(
        "vector_job_states",
        sa.Column("arq_job_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "vector_job_states",
        sa.Column("worker_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "vector_job_states",
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vector_job_states",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vector_job_states",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vector_job_states",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vector_job_states",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # 先删新增字段
    op.drop_column("vector_job_states", "last_heartbeat_at")
    op.drop_column("vector_job_states", "finished_at")
    op.drop_column("vector_job_states", "started_at")
    op.drop_column("vector_job_states", "claimed_at")
    op.drop_column("vector_job_states", "enqueued_at")
    op.drop_column("vector_job_states", "worker_id")
    op.drop_column("vector_job_states", "arq_job_id")

    # PG 不支持 ALTER TYPE DROP VALUE; 如需彻底回滚需手动重建类型。
    # 这里仅在 SQLite 等方言下做尽力回滚, PG 保留扩展后的枚举值(业务层不再写入即可)。
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "postgresql":
        return
    # 回滚枚举值到 4 状态: 先把已有行改回兼容值
    op.execute(
        "UPDATE vector_job_states SET status = 'pending' "
        "WHERE status IN ('queued', 'claimed', 'recovering', 'lost')"
    )
