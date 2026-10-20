"""add llm_weekly_advice and llm_priority_snapshots for weekly advisor

Revision ID: e5a9b2c3d7f1
Revises: d4e8f1a2b6c0
Create Date: 2026-08-11 16:40:00.000000+08:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e5a9b2c3d7f1"
down_revision: Union[str, None] = "d4e8f1a2b6c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_weekly_advice",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("week_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("target", sa.String(length=80), nullable=False),
        sa.Column("advice_action", sa.String(length=32), nullable=False),
        sa.Column("recommended_config_id", sa.String(length=64), nullable=True),
        sa.Column("demote_config_id", sa.String(length=64), nullable=True),
        sa.Column("proposed_priority", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("applied_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_llm_weekly_advice_week", "llm_weekly_advice", ["week_start"])
    op.create_index("idx_llm_weekly_advice_status", "llm_weekly_advice", ["status"])
    op.create_index(
        "uq_llm_weekly_advice_week_scope_target",
        "llm_weekly_advice",
        ["week_start", "scope", "target"],
        unique=True,
    )

    op.create_table(
        "llm_priority_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("priority_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False, server_default="advice_apply"),
        sa.Column("advice_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_llm_priority_snapshots_purpose", "llm_priority_snapshots", ["purpose"])
    op.create_index("idx_llm_priority_snapshots_advice", "llm_priority_snapshots", ["advice_id"])


def downgrade() -> None:
    op.drop_index("idx_llm_priority_snapshots_advice", table_name="llm_priority_snapshots")
    op.drop_index("idx_llm_priority_snapshots_purpose", table_name="llm_priority_snapshots")
    op.drop_table("llm_priority_snapshots")
    op.drop_index("uq_llm_weekly_advice_week_scope_target", table_name="llm_weekly_advice")
    op.drop_index("idx_llm_weekly_advice_status", table_name="llm_weekly_advice")
    op.drop_index("idx_llm_weekly_advice_week", table_name="llm_weekly_advice")
    op.drop_table("llm_weekly_advice")
