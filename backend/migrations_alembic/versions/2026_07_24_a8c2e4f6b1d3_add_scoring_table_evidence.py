"""add_scoring_table_evidence

评分原始表证据快照(候选事务证据层, Task2):
- documents.scoring_table_evidence: 装配前从 ParsedDocument 评分区段逐张捕获的
  完整物理表JSON(page/table_index/bbox/来源类型/全部单元格原文, 不截断)。

存量文档不回填(None 表示未捕获, 候选事务提取器降级处理)。

Revision ID: a8c2e4f6b1d3
Revises: f7a9c1d3e5b8
Create Date: 2026-07-24 20:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a8c2e4f6b1d3"
down_revision: Union[str, None] = "f7a9c1d3e5b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "scoring_table_evidence", sa.JSON(), nullable=True,
            comment="评分原始表证据快照JSON(装配前完整物理表)",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "scoring_table_evidence")
