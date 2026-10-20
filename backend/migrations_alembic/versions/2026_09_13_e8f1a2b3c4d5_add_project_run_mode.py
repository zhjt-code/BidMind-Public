"""add_project_run_mode

项目运行模式: guided=人工确认(默认) / express=一步到位。
存量行回填 guided。不使用 PostgreSQL ENUM，避免 pg_enum 迁移。

Revision ID: e8f1a2b3c4d5
Revises: f7c2a9d4e6b8
Create Date: 2026-09-13 20:50:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e8f1a2b3c4d5"
down_revision: Union[str, None] = "f7c2a9d4e6b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "run_mode",
            sa.String(length=20),
            nullable=False,
            server_default="guided",
            comment="运行模式: guided=人工确认 / express=一步到位",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "run_mode")
