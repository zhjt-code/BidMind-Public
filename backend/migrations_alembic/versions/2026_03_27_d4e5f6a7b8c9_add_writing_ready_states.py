"""add writing ready-state enum values

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-27

Refactor: 拆分任务运行态与就绪态
- OUTLINED  : 目录生成完成,等待编辑/上传素材
- MATCHING  : 素材匹配中(替代 MATERIAL_BINDING 语义)
- MATCHED   : 匹配完成,等待确认绑定
- COMPOSED  : 内容生成完成,等待编辑/渲染
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'OUTLINED'")
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'MATCHING'")
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'MATCHED'")
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'COMPOSED'")


def downgrade() -> None:
    pass
