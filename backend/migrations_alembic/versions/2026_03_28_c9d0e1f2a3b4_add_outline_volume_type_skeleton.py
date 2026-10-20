"""add volume_type and is_skeleton to writing_outline_nodes

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-03-28

目录照搬模式: 新增 volume_type(所属分卷) 和 is_skeleton(骨架锁定) 字段
- volume_type: technical/commercial, nullable
- is_skeleton: 0/1, default 0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "writing_outline_nodes",
        sa.Column("volume_type", sa.String(20), nullable=True,
                  comment="所属分卷: technical/commercial"),
    )
    op.add_column(
        "writing_outline_nodes",
        sa.Column("is_skeleton", sa.Integer(), server_default="0",
                  nullable=False,
                  comment="是否为骨架节点(招标文件指定,不可删除): 0/1"),
    )


def downgrade() -> None:
    op.drop_column("writing_outline_nodes", "is_skeleton")
    op.drop_column("writing_outline_nodes", "volume_type")
