"""add_writing_clause_page_range

 后续：将 writing_clauses.source_page 从"伪精确单页"语义拆成
批次页范围(source_page_start/source_page_end)，source_page 保留为默认
打开 PDF 用的弱锚点，不再承担精确展示职责。

Revision ID: e8f9a0b1c2d3
Revises: 6d1c9f2b3a4e
Create Date: 2026-04-22 10:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "6d1c9f2b3a4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "writing_clauses",
        sa.Column(
            "source_page_start",
            sa.Integer(),
            nullable=True,
            comment="条款所属批次起始页(诚实页范围下界)",
        ),
    )
    op.add_column(
        "writing_clauses",
        sa.Column(
            "source_page_end",
            sa.Integer(),
            nullable=True,
            comment="条款所属批次结束页(诚实页范围上界)",
        ),
    )
    # 既有数据兜底: 历史行若 source_page 已有值, 则 start/end 同时置为 source_page,
    # 避免前端在范围展示时对老数据 fallback 失败导致"P?-P?"。
    op.execute(
        "UPDATE writing_clauses "
        "SET source_page_start = source_page, "
        "    source_page_end = source_page "
        "WHERE source_page IS NOT NULL "
        "  AND source_page_start IS NULL "
        "  AND source_page_end IS NULL"
    )


def downgrade() -> None:
    op.drop_column("writing_clauses", "source_page_end")
    op.drop_column("writing_clauses", "source_page_start")
