"""add_requirement_scoring_fields

Revision ID: d3e5f7a9b1c2
Revises: c2d4e6f8a0b1
Create Date: 2026-06-13

为 requirements 增加评分项结构化字段（P2 评分表聚焦）：
- requirement_kind: normal(普通要求) / scoring_item(评分项)
- score_weight: 评分项满分分值
- is_key_review: 是否重点评审项（评分项默认 1）
- scoring_metadata: 评分项结构化元数据(JSON, 评分项名/分档规则/扣分规则/所属卷)

存量行回填 requirement_kind='normal' / is_key_review=0，行为与现状一致。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3e5f7a9b1c2"
down_revision: Union[str, None] = "c2d4e6f8a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column(
            "requirement_kind",
            sa.String(length=20),
            nullable=False,
            server_default="normal",
            comment="要求种类: normal/scoring_item",
        ),
    )
    op.add_column(
        "requirements",
        sa.Column(
            "score_weight",
            sa.Float(),
            nullable=True,
            comment="评分项满分分值(仅 scoring_item)",
        ),
    )
    op.add_column(
        "requirements",
        sa.Column(
            "is_key_review",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="是否重点评审项(评分项默认1): 0/1",
        ),
    )
    op.add_column(
        "requirements",
        sa.Column(
            "scoring_metadata",
            sa.JSON(),
            nullable=True,
            comment="评分项结构化元数据(JSON)",
        ),
    )


def downgrade() -> None:
    op.drop_column("requirements", "scoring_metadata")
    op.drop_column("requirements", "is_key_review")
    op.drop_column("requirements", "score_weight")
    op.drop_column("requirements", "requirement_kind")
