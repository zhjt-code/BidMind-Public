"""add_scoring_candidate_report

评分候选事务脱敏报告持久化(Task6A):
- documents.scoring_candidate_report: 装配前证据串联
  build_table_evidence→structure_reader→candidate_reader→table_validator→
  document_transaction后, 按shadow/enforce模式产出的脱敏诊断快照
  (status/mode/candidate_total/problem_tables, 不含单元格正文/LLM原始响应)。

存量文档不回填(None 表示未评估过, off 模式恒为 None)。

Revision ID: b9d3f5a7c2e4
Revises: a8c2e4f6b1d3
Create Date: 2026-07-24 21:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b9d3f5a7c2e4"
down_revision: Union[str, None] = "a8c2e4f6b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "scoring_candidate_report", sa.JSON(), nullable=True,
            comment="评分表统一候选事务脱敏报告",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "scoring_candidate_report")
