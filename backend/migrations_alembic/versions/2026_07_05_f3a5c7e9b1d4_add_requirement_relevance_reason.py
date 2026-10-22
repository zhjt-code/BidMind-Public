"""add_requirement_relevance_reason

Revision ID: f3a5c7e9b1d4
Revises: b9d4e1f6a2c8
Create Date: 2026-07-05

为 requirements 增加评审意义归因字段 relevance_reason（P2.1）：
记录 review_relevance 折叠/豁免的判定来源，供确认页归因徽标展示与误判回流分析。
取值（可空，actionable 默认不写）：
- llm_locus: LLM 证据载体判定折叠
- rule_floor: 噪音规则库/占位符正则命中折叠
- contract_section: 合同区段启发式折叠
- behavioral_mandatory: 否决项行为性双确认折叠（风险告知）
- manual: 人工升降级
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a5c7e9b1d4"
down_revision: Union[str, None] = "b9d4e1f6a2c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column(
            "relevance_reason",
            sa.String(length=40),
            nullable=True,
            comment="评审意义归因: llm_locus/rule_floor/contract_section/behavioral_mandatory/manual",
        ),
    )


def downgrade() -> None:
    op.drop_column("requirements", "relevance_reason")
