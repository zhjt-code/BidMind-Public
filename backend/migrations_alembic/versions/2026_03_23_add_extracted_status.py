"""add EXTRACTED value to projectstatus enum

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-23

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'EXTRACTED'"
    )


def downgrade() -> None:
    # ALTER TYPE ... ADD VALUE is not transactional in PostgreSQL;
    # removing an enum value requires recreating the type, which is
    # intentionally omitted here to avoid data loss.
    pass
