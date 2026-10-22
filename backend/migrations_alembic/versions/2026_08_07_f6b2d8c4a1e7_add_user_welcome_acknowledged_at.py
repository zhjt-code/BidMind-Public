"""add user welcome acknowledged at

Revision ID: f6b2d8c4a1e7
Revises: a3f7c2d9e1b5
Create Date: 2026-08-07 16:10:00.000000+08:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f6b2d8c4a1e7"
down_revision: Union[str, None] = "a3f7c2d9e1b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "welcome_letter_acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="欢迎信确认时间; NULL 表示待展示",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "welcome_letter_acknowledged_at")
