"""add PARSED value to projectstatus enum

Revision ID: c6e8a0b2d4f1
Revises: f8a2c4d6e1b0
Create Date: 2026-09-08

与回填拆成两个 revision，且 ADD VALUE 必须 autocommit：
env.py 把一次 upgrade 包在同一事务里，仅拆 revision 仍会 UnsafeNewEnumValueUsage。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c6e8a0b2d4f1"
down_revision: Union[str, None] = "f8a2c4d6e1b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'PARSED'")


def downgrade() -> None:
    # ALTER TYPE ... ADD VALUE 不可安全回滚，避免重建类型丢数据
    pass
