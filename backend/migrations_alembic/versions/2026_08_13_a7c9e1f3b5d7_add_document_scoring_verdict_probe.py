"""add_document_scoring_verdict_probe

评分 verdict 探针(阶段2 VLM-first 裁决数据):
- documents.scoring_verdict_probe: 最近一次评分提取的判绿探针JSON
  (trigger_verdict/rescue_adopted/rescue_rounds/final_needs_review/at)。

存量文档不回填(None 表示窗口内无提取行为, 不计入统计样本)。

Revision ID: a7c9e1f3b5d7
Revises: e5a9b2c3d7f1
Create Date: 2026-08-13 16:40:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a7c9e1f3b5d7"
down_revision: Union[str, None] = "e5a9b2c3d7f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "scoring_verdict_probe", sa.JSON(), nullable=True,
            comment="最近一次评分提取的verdict探针JSON(阶段2 VLM-first裁决数据)",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "scoring_verdict_probe")
