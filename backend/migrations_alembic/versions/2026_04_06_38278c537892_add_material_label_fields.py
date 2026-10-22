"""add_material_label_fields

Revision ID: 38278c537892
Revises: 9f1a2b3c4d5e
Create Date: 2026-04-06 17:14:51.669250+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '38278c537892'
down_revision: Union[str, None] = '9f1a2b3c4d5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('writing_materials', sa.Column('file_name_label', sa.String(length=100), nullable=True, comment='文件名规则解析的业务类型细分标签'))
    op.add_column('writing_materials', sa.Column('content_label', sa.String(length=100), nullable=True, comment='VL/OCR/正文识别的业务类型细分标签'))
    op.add_column('writing_materials', sa.Column('resolved_label', sa.String(length=100), nullable=True, comment='最终决议的业务类型标签(全链路唯一真源)'))
    op.add_column('writing_materials', sa.Column('label_conflict', sa.String(length=20), nullable=True, comment='标签冲突状态: matched/conflicted/uncertain'))
    op.add_column('writing_materials', sa.Column('label_source', sa.String(length=20), nullable=True, comment='决议来源: filename/vl/llm/rule'))


def downgrade() -> None:
    op.drop_column('writing_materials', 'label_source')
    op.drop_column('writing_materials', 'label_conflict')
    op.drop_column('writing_materials', 'resolved_label')
    op.drop_column('writing_materials', 'content_label')
    op.drop_column('writing_materials', 'file_name_label')
