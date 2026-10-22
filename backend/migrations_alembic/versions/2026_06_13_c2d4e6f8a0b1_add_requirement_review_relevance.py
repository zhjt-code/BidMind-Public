"""add_requirement_review_relevance

Revision ID: c2d4e6f8a0b1
Revises: b1f2e3d4c5a7
Create Date: 2026-06-13

为 requirements 增加评审意义分级字段 review_relevance（P1 商务降噪）：
- actionable: 文本必须响应类，进入审查打分（存量默认）
- informational: 信息告知/合同文本类，确认页默认折叠，不进审查打分
- procedural: 程序/递交/履约过程类，确认页默认折叠，只作提醒

存量行统一回填 actionable，行为与现状一致（默认全部进入审查）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d4e6f8a0b1"
down_revision: Union[str, None] = 'e5f7a9c1d3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column(
            "review_relevance",
            sa.String(length=20),
            nullable=False,
            server_default="actionable",
            comment="评审意义分级: actionable/informational/procedural",
        ),
    )


def downgrade() -> None:
    op.drop_column("requirements", "review_relevance")
