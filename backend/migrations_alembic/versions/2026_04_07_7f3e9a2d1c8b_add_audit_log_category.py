"""add_audit_log_category

Revision ID: 7f3e9a2d1c8b
Revises: b7c8d9e0f1a2
Create Date: 2026-04-07 10:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f3e9a2d1c8b"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("category", sa.String(length=30), nullable=True),
    )

    # 回填已有数据
    op.execute(
        "UPDATE audit_logs SET category = 'bid_review' "
        "WHERE resource_type IN ('requirement', 'review', 'report')"
    )
    op.execute(
        "UPDATE audit_logs SET category = 'system' "
        "WHERE resource_type = 'user'"
    )
    op.execute(
        "UPDATE audit_logs SET category = LOWER(p.project_type::text) "
        "FROM projects p "
        "WHERE audit_logs.resource_type = 'project' "
        "AND audit_logs.resource_id = p.id "
        "AND audit_logs.category IS NULL"
    )
    op.execute(
        "UPDATE audit_logs SET category = 'system' WHERE category IS NULL"
    )
    op.execute(
        "UPDATE audit_logs SET category = LOWER(category) WHERE category != LOWER(category)"
    )

    op.alter_column(
        "audit_logs",
        "category",
        nullable=False,
        server_default="system",
    )
    op.create_index(
        op.f("ix_audit_logs_category"), "audit_logs", ["category"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_category"), table_name="audit_logs")
    op.drop_column("audit_logs", "category")
