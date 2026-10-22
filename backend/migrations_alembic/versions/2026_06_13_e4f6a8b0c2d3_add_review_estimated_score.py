"""add_review_estimated_score

Revision ID: e4f6a8b0c2d3
Revises: d3e5f7a9b1c2
Create Date: 2026-06-13

为 review_results 增加评分项 AI 预估得分字段 estimated_score（P2 闭环）：
- 仅 scoring_item 对应的审查结果填写；满分取 requirement.score_weight。
- 用于结果页中栏展示"满分/预估得分"与汇总"预估总得分"。

存量行为 NULL（前端展示为"未预估"），不影响既有审查结论。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f6a8b0c2d3"
down_revision: Union[str, None] = "d3e5f7a9b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "review_results",
        sa.Column(
            "estimated_score",
            sa.Float(),
            nullable=True,
            comment="评分项 AI 预估得分(仅 scoring_item)",
        ),
    )


def downgrade() -> None:
    op.drop_column("review_results", "estimated_score")
