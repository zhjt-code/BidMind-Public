"""add_writing_material_usage_metrics

写标素材计费计量字段:
- writing_materials.page_count: 素材文件页数(批处理解析时写入)
- writing_materials.ocr_image_count: OCR处理图片数(批处理识别时写入)

素材是写标业务的输入资源,此前页数/OCR张数无持久化,
计费口径(BillingGuard._collect_metrics)仅聚合 documents 表导致素材维度全部漏计。

Revision ID: c7d9e1f3a5b7
Revises: b6c8d0e2f4a6
Create Date: 2026-07-11 18:30:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7d9e1f3a5b7'
down_revision: Union[str, None] = 'b6c8d0e2f4a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'writing_materials',
        sa.Column(
            'page_count', sa.Integer(), nullable=True,
            server_default='0', comment='素材文件页数(批处理解析时写入,计费用)',
        ),
    )
    op.add_column(
        'writing_materials',
        sa.Column(
            'ocr_image_count', sa.Integer(), nullable=True,
            server_default='0', comment='OCR处理图片数(批处理识别时写入,计费用)',
        ),
    )


def downgrade() -> None:
    op.drop_column('writing_materials', 'ocr_image_count')
    op.drop_column('writing_materials', 'page_count')
