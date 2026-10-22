"""add prompt_versions table

Revision ID: f9442dd50225
Revises: 78ffe534a070
Create Date: 2026-03-18 11:39:56.658440+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f9442dd50225'
down_revision: Union[str, None] = '78ffe534a070'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'prompt_versions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('purpose', sa.String(length=50), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('name', sa.String(length=200), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('purpose', 'version', name='uq_prompt_purpose_version'),
    )
    op.create_index(op.f('ix_prompt_versions_id'), 'prompt_versions', ['id'])
    op.create_index(op.f('ix_prompt_versions_purpose'), 'prompt_versions', ['purpose'])
    op.create_index(op.f('ix_prompt_versions_is_active'), 'prompt_versions', ['is_active'])


def downgrade() -> None:
    op.drop_index(op.f('ix_prompt_versions_is_active'), table_name='prompt_versions')
    op.drop_index(op.f('ix_prompt_versions_purpose'), table_name='prompt_versions')
    op.drop_index(op.f('ix_prompt_versions_id'), table_name='prompt_versions')
    op.drop_table('prompt_versions')
