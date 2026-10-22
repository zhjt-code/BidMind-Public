"""add_writing_clause_is_manual

Revision ID: b7c8d9e0f1a2
Revises: 6f4b8d1c2a9e
Create Date: 2026-04-06 22:30:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "6f4b8d1c2a9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "writing_clauses",
        sa.Column(
            "is_manual",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="是否为人工新增: 0/1",
        ),
    )
    op.alter_column("writing_clauses", "is_manual", server_default=None)


def downgrade() -> None:
    op.drop_column("writing_clauses", "is_manual")
