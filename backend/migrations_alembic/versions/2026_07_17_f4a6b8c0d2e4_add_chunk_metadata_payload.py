"""add_chunk_metadata_payload

chunk 结构化载荷列(评分提取综合提升 阶段1.5, evidence bundle 最小版):
- chunks.chunk_metadata: JSONB, 承载 scoring_payload(网格/lot/表格指纹/页范围),
  消除"markdown 文本纹协议往返"——评分提取阶段直接消费结构化网格,
  markdown content 降级为展示与 LLM 输入副本。

存量 chunk 不回填(可空), 提取侧按"payload 缺失回退 markdown 解析"双轨兼容。

Revision ID: f4a6b8c0d2e4
Revises: e3f5a7b9c1d3
Create Date: 2026-07-17 13:30:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f4a6b8c0d2e4'
down_revision: Union[str, None] = 'e3f5a7b9c1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'chunks',
        sa.Column(
            'chunk_metadata', postgresql.JSONB(), nullable=True,
            comment='结构化载荷(scoring_payload: 网格/lot/表格指纹/页范围)',
        ),
    )


def downgrade() -> None:
    op.drop_column('chunks', 'chunk_metadata')
