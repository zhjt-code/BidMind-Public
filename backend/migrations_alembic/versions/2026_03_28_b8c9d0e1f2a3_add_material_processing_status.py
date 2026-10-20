"""add processing_status to writing_materials

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-03-28

素材上传批处理重构: 新增 processing_status 字段支持两阶段流程
- saved: 文件已保存,等待批处理
- processing: 批处理进行中
- completed: 处理完成
- failed: 处理失败
历史数据默认 completed(已完成)
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE writing_materials "
        "ADD COLUMN IF NOT EXISTS processing_status VARCHAR(20) "
        "NOT NULL DEFAULT 'completed'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE writing_materials "
        "DROP COLUMN IF EXISTS processing_status"
    )
