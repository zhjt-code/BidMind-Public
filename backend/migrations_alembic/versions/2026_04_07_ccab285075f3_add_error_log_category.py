"""add_error_log_category

Revision ID: ccab285075f3
Revises: 7f3e9a2d1c8b
Create Date: 2026-04-07 17:48:26.120109+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ccab285075f3'
down_revision: Union[str, None] = '7f3e9a2d1c8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "system_error_logs",
        sa.Column("category", sa.String(length=30), nullable=True),
    )

    op.execute(
        "UPDATE system_error_logs SET category = 'bid_review' "
        "WHERE task_type IN ('process', 'extract', 'review')"
    )
    op.execute(
        "UPDATE system_error_logs SET category = 'collusion_check' "
        "WHERE task_type = 'collusion'"
    )
    op.execute(
        "UPDATE system_error_logs SET category = 'bid_writing' "
        "WHERE task_type LIKE 'writing_%'"
    )
    op.execute(
        "UPDATE system_error_logs SET category = 'system' "
        "WHERE category IS NULL"
    )

    op.alter_column(
        "system_error_logs",
        "category",
        nullable=False,
        server_default="system",
    )
    op.create_index(
        op.f("ix_system_error_logs_category"),
        "system_error_logs",
        ["category"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_system_error_logs_category"),
        table_name="system_error_logs",
    )
    op.drop_column("system_error_logs", "category")
