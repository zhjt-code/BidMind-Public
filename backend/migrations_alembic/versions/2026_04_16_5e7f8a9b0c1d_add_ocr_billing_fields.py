"""add_ocr_billing_fields

Revision ID: 5e7f8a9b0c1d
Revises: a0b1c2d3e4f5
Create Date: 2026-04-16 10:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5e7f8a9b0c1d'
down_revision: Union[str, None] = 'a0b1c2d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'documents',
        sa.Column(
            'ocr_image_count', sa.Integer(), nullable=True,
            server_default='0', comment='OCR处理图片数',
        ),
    )


def downgrade() -> None:
    op.drop_column('documents', 'ocr_image_count')
