"""add_document_scoring_signals

评分结构信号快照(SchemeReader 阶段0):
- documents.scoring_signals: 装配前从 ParsedDocument 评分区段逐字捕获的
  结构边界信号JSON(声明句/表头行/合计行/行骨架/标题样行, 带锚点)。

存量文档不回填(None 表示未捕获, 判读器降级处理)。

Revision ID: f7a9c1d3e5b8
Revises: e5b7d9f1a3c5
Create Date: 2026-07-24 16:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f7a9c1d3e5b8"
down_revision: Union[str, None] = "e5b7d9f1a3c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "scoring_signals", sa.JSON(), nullable=True,
            comment="评分结构边界信号快照JSON",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "scoring_signals")
