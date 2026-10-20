"""add_document_integrity_hashes

文档完整性双指纹(评分提取综合提升 阶段0.3, 缺口D):
- documents.file_hash: 上传字节 sha256(字节级副本检测, 样本13=14实证)
- documents.content_hash: 解析后归一化全文 sha256(逻辑副本检测, 样本19≈21实证)

存量文档不回填(可空), 新上传/新解析时写入。

Revision ID: e3f5a7b9c1d3
Revises: d8e2f4a6b9c1
Create Date: 2026-07-17 12:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e3f5a7b9c1d3'
down_revision: Union[str, None] = 'd8e2f4a6b9c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'documents',
        sa.Column(
            'file_hash', sa.String(length=64), nullable=True,
            comment='上传文件字节sha256(字节级副本检测)',
        ),
    )
    op.add_column(
        'documents',
        sa.Column(
            'content_hash', sa.String(length=64), nullable=True,
            comment='解析后归一化全文sha256(逻辑副本检测)',
        ),
    )
    op.create_index('ix_documents_file_hash', 'documents', ['file_hash'])


def downgrade() -> None:
    op.drop_index('ix_documents_file_hash', table_name='documents')
    op.drop_column('documents', 'content_hash')
    op.drop_column('documents', 'file_hash')
