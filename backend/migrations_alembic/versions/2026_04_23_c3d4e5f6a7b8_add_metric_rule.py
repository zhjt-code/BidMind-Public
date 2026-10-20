"""add_metric_rule

Phase 4 监控体系: 新增 metric_rule 表, 支持可配置的趋势判断规则.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-23 13:00:00.000000+08:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c45mr6d7e8f9"
down_revision: Union[str, None] = "b34mc5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metric_rule",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("rule_type", sa.String(length=32), nullable=False, server_default="trend"),
        sa.Column("algorithm", sa.String(length=32), nullable=False, server_default="ratio"),
        sa.Column("params_json", sa.Text(), nullable=True),
        sa.Column("verdict_map_json", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_metric_rule_metric_name", "metric_rule", ["metric_name"])
    op.create_index("ix_metric_rule_enabled", "metric_rule", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_metric_rule_enabled", table_name="metric_rule")
    op.drop_index("ix_metric_rule_metric_name", table_name="metric_rule")
    op.drop_table("metric_rule")
