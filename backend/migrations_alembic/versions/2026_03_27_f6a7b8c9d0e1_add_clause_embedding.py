"""add embedding column to writing_clauses

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-27

素材处理链升级 Phase 2: 为 WritingClause 添加 embedding 字段
- 用于约束与素材的向量语义匹配
- nullable=True，兼容已有数据
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE writing_clauses ADD COLUMN IF NOT EXISTS embedding vector(1024)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE writing_clauses DROP COLUMN IF EXISTS embedding"
    )
