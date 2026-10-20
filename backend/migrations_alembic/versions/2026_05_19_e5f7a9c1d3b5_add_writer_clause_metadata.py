"""add_writer_clause_metadata

Revision ID: e5f7a9c1d3b5
Revises: d4f6a8c0e2b3
Create Date: 2026-05-19

为写标约束提取补充结构化元数据承载字段，并为渲染配置记录
提交/装订/签章/暗标等不可完全自动渲染的格式风险要求。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f7a9c1d3b5"
down_revision: Union[str, None] = "d4f6a8c0e2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "writing_clauses",
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=True,
            comment="结构化补充信息(JSON): 评分梯度/材料类型/适用章节/证据要求等",
        ),
    )
    op.add_column(
        "writing_render_profiles",
        sa.Column(
            "submission_requirements",
            sa.JSON(),
            nullable=True,
            comment="提交/装订/签章/暗标等不可完全自动渲染的格式风险要求(JSON)",
        ),
    )


def downgrade() -> None:
    op.drop_column("writing_render_profiles", "submission_requirements")
    op.drop_column("writing_clauses", "metadata_json")
