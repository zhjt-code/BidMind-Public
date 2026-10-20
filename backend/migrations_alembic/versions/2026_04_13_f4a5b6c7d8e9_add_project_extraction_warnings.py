"""add_project_extraction_warnings

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-04-13

为 projects 表添加 extraction_warnings JSON 列，持久化招标要求提取阶段的分块失败警告。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "extraction_warnings",
            sa.JSON(),
            nullable=True,
            comment="招标要求提取阶段的警告信息（如分块失败提示）",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "extraction_warnings")
