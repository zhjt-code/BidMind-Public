"""add chapter_profile to outline_nodes and reason/scope to bindings

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-03-29

三元匹配引擎重写:
- writing_outline_nodes.chapter_profile: 章节画像JSON(semantic_role/expects/rejects)
- writing_bindings.reason: AI匹配理由
- writing_bindings.scope: 约束作用域(global/exclusive/shared/orphan)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "writing_outline_nodes",
        sa.Column("chapter_profile", sa.Text(), nullable=True,
                  comment="章节画像JSON(semantic_role/expects/rejects)"),
    )
    op.add_column(
        "writing_bindings",
        sa.Column("reason", sa.String(300), nullable=True,
                  comment="AI匹配理由"),
    )
    op.add_column(
        "writing_bindings",
        sa.Column("scope", sa.String(20), nullable=True,
                  comment="约束作用域: global/exclusive/shared/orphan"),
    )


def downgrade() -> None:
    op.drop_column("writing_bindings", "scope")
    op.drop_column("writing_bindings", "reason")
    op.drop_column("writing_outline_nodes", "chapter_profile")
