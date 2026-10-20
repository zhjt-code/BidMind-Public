"""add_document_image_provenance

为消耗记录透明度契约补充图片识别来源字段:
- documents.image_count: 解析提取图片总数
- documents.vl_skipped: 本次处理是否跳过图片识别(null=历史数据未知)

Revision ID: b6c8d0e2f4a6
Revises: f3a5c7e9b1d4
Create Date: 2026-07-09 12:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b6c8d0e2f4a6'
down_revision: Union[str, None] = 'f3a5c7e9b1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'documents',
        sa.Column(
            'image_count', sa.Integer(), nullable=True,
            server_default='0', comment='解析提取图片总数',
        ),
    )
    op.add_column(
        'documents',
        sa.Column(
            'vl_skipped', sa.Boolean(), nullable=True,
            comment='本次处理是否跳过图片识别(null=历史未知)',
        ),
    )


def downgrade() -> None:
    op.drop_column('documents', 'vl_skipped')
    op.drop_column('documents', 'image_count')
