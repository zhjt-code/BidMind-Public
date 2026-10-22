"""add_writing_material_display_title

Revision ID: f1e2d3c4b5a6
Revises: a9b8c7d6e5f4
Create Date: 2026-04-08

为 writing_materials 增加用户修正标题字段：
- display_title
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE writing_materials ADD COLUMN IF NOT EXISTS display_title VARCHAR(500)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE writing_materials DROP COLUMN IF EXISTS display_title")

