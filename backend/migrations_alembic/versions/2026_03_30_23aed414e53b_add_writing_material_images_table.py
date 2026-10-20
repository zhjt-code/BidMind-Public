"""add_writing_material_images_table

Revision ID: 23aed414e53b
Revises: d1e2f3a4b5c6
Create Date: 2026-03-30 17:32:56.467586+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '23aed414e53b'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE imagetype AS ENUM "
        "('license','personnel_cert','qualification','nameplate','photo','diagram','other'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$;"
    )

    op.create_table(
        'writing_material_images',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            'material_id', sa.Integer(),
            sa.ForeignKey('writing_materials.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('image_path', sa.String(length=500), nullable=False,
                   comment='图片磁盘存储路径'),
        sa.Column('image_index', sa.Integer(), nullable=False, server_default='0',
                   comment='在素材中的序号(从0开始)'),
        sa.Column('vl_content', sa.Text(), nullable=True,
                   comment='VL模型原始识别文本'),
        sa.Column(
            'image_type',
            postgresql.ENUM(
                'license', 'personnel_cert', 'qualification', 'nameplate',
                'photo', 'diagram', 'other',
                name='imagetype',
                create_type=False,
            ),
            nullable=True,
            comment='图片类型(7种子类型)',
        ),
        sa.Column('caption', sa.String(length=500), nullable=True,
                   comment='自动生成的标注文本(所有类型均有)'),
        sa.Column('structured_fields', sa.Text(), nullable=True,
                   comment='结构化字段(JSON),仅可结构化类型有值'),
        sa.Column('width', sa.Integer(), nullable=False, server_default='0',
                   comment='图片宽度(px)'),
        sa.Column('height', sa.Integer(), nullable=False, server_default='0',
                   comment='图片高度(px)'),
        sa.Column('file_hash', sa.String(length=64), nullable=True,
                   comment='文件内容SHA-256哈希(前64KB),用于跨素材去重'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                   server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                   server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_writing_material_images_id', 'writing_material_images', ['id'])
    op.create_index('ix_writing_material_images_material_id', 'writing_material_images', ['material_id'])
    op.create_index('ix_writing_material_images_file_hash', 'writing_material_images', ['file_hash'])


def downgrade() -> None:
    op.drop_index('ix_writing_material_images_file_hash', table_name='writing_material_images')
    op.drop_index('ix_writing_material_images_material_id', table_name='writing_material_images')
    op.drop_index('ix_writing_material_images_id', table_name='writing_material_images')
    op.drop_table('writing_material_images')
    op.execute("DROP TYPE IF EXISTS imagetype")
