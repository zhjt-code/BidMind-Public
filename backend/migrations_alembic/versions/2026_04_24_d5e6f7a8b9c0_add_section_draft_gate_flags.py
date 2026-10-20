"""add_section_draft_gate_flags

Revision ID: d5e6f7a8b9c0
Revises: c45mr6d7e8f9
Create Date: 2026-04-24

P2.1a: 为 writing_section_drafts 新增 gate_flags JSON 列, 存放单章级 Gate 信号,
供 WritingCompose 前端渲染橙标 (素材未引用 / 表格填充率 / AI 套话密度等)。

向后兼容: 字段 nullable=True, 历史记录为 NULL, 不影响现有消费。
部署顺序: 先部署后端 (安全写入), 再部署前端 (读取展示)。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c45mr6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE writing_section_drafts "
        "ADD COLUMN IF NOT EXISTS gate_flags JSONB"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE writing_section_drafts "
        "DROP COLUMN IF EXISTS gate_flags"
    )
