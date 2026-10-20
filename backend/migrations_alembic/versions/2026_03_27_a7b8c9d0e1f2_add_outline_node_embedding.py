"""add embedding column to writing_outline_nodes

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-27

素材处理链升级 Phase 2: 为 WritingOutlineNode 添加 embedding 字段
- 用于目录节点与素材的向量语义匹配
- nullable=True，兼容已有数据
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE writing_outline_nodes ADD COLUMN IF NOT EXISTS embedding vector(1024)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE writing_outline_nodes DROP COLUMN IF EXISTS embedding"
    )
