"""add_project_knowledge_usage_config

Revision ID: a1c2d3e4f5b6
Revises: f0a1b2c3d4e5
Create Date: 2026-05-09

为 projects 表新增 knowledge_usage_config JSON 列，持久化用户在投标审查/串标审查
入口对知识库的显式授权与所选资料 IDs；nullable，历史项目默认未启用。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1c2d3e4f5b6"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "knowledge_usage_config",
            sa.JSON(),
            nullable=True,
            comment=(
                "项目级知识库使用配置: enabled / flow / "
                "selected_kb_document_ids / usage_policy / "
                "confirmed_by_user / confirmed_at"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "knowledge_usage_config")
