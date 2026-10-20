"""add_review_result_rule_assessments

Wave2-S5 P1 B3: 评分项逐规则判定结果持久化字段。

review_results.rule_assessments (JSON/JSONB):
- 存储 LLM 输出的逐条评分规则判定结果
- 结构: [{rule_excerpt, status, points, reason, evidence_pages}]
- 老数据为 null（向后兼容）

Revision ID: d8e2f4a6b9c1
Revises: c7d9e1f3a5b7
Create Date: 2026-07-14 11:45:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd8e2f4a6b9c1'
down_revision: Union[str, None] = 'c7d9e1f3a5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'review_results',
        sa.Column(
            'rule_assessments',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='评分项逐规则判定列表 [{rule_excerpt,status,points,reason,evidence_pages}]（老数据为 null）',
        ),
    )


def downgrade() -> None:
    op.drop_column('review_results', 'rule_assessments')
