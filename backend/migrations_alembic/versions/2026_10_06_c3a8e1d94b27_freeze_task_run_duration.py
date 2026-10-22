"""freeze completed task run duration

控制台耗时改读 task_runs。已完成且尚未冻结的行，用更新时间与创建时间的差值回填一次。

Revision ID: c3a8e1d94b27
Revises: b4c6d8e0f2a1
Create Date: 2026-10-06 09:50:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3a8e1d94b27"
down_revision: Union[str, None] = "b4c6d8e0f2a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_runs",
        sa.Column("duration_ms", sa.Integer(), nullable=True, comment="首次进入完成态时冻结的耗时毫秒"),
    )
    op.execute(
        """
        UPDATE task_runs
        SET duration_ms = CAST(EXTRACT(EPOCH FROM (updated_at - created_at)) * 1000 AS INTEGER)
        WHERE status = 'completed'
          AND duration_ms IS NULL
          AND updated_at > created_at
        """
    )


def downgrade() -> None:
    op.drop_column("task_runs", "duration_ms")
