"""add_writing_section_plans

Revision ID: c1e3f5a7b9d2
Revises: a9c1e3f5b7d9
Create Date: 2026-07-22 10:40:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c1e3f5a7b9d2"
down_revision: Union[str, None] = "a9c1e3f5b7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "writing_section_plans",
        sa.Column("project_id", sa.Integer(), nullable=False, comment="所属写标项目ID"),
        sa.Column("outline_node_id", sa.Integer(), nullable=False, comment="所属目录节点ID"),
        sa.Column("version", sa.Integer(), nullable=False, comment="同一节点的计划版本号"),
        sa.Column("schema_version", sa.String(length=40), nullable=False, comment="SectionPlan契约版本"),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False, comment="规划输入稳定指纹"),
        sa.Column("input_manifest", postgresql.JSONB(), nullable=False, comment="规划输入清单"),
        sa.Column("prompt_snapshot", postgresql.JSONB(), nullable=False, comment="Prompt版本和哈希快照"),
        sa.Column("plan_payload", postgresql.JSONB(), nullable=True, comment="已校验SectionPlan payload"),
        sa.Column("status", sa.String(length=20), nullable=False, comment="planning/ready/failed"),
        sa.Column("is_current", sa.Integer(), nullable=False, server_default="0", comment="是否当前ready计划"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0", comment="规划尝试次数"),
        sa.Column("attempt_trace", postgresql.JSONB(), nullable=True, comment="失败/接管摘要"),
        sa.Column("generation_token", sa.String(length=64), nullable=True, comment="租约接管token"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True, comment="租约过期时间"),
        sa.Column("failure_code", sa.String(length=80), nullable=True, comment="失败码"),
        sa.Column("failure_reason", sa.Text(), nullable=True, comment="失败摘要"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True, comment="规划完成时间"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status in ('planning', 'ready', 'failed')", name="ck_writing_section_plans_status"),
        sa.CheckConstraint("is_current in (0, 1)", name="ck_writing_section_plans_is_current"),
        sa.ForeignKeyConstraint(["outline_node_id"], ["writing_outline_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("outline_node_id", "version", name="uq_writing_section_plans_node_version"),
    )
    op.create_index(
        "ix_writing_section_plans_project_id",
        "writing_section_plans",
        ["project_id"],
    )
    op.create_index(
        "ix_writing_section_plans_outline_node_id",
        "writing_section_plans",
        ["outline_node_id"],
    )
    op.create_index(
        "uq_writing_section_plans_current_ready",
        "writing_section_plans",
        ["outline_node_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ready' AND is_current = 1"),
    )
    op.create_index(
        "uq_writing_section_plans_ready_fingerprint",
        "writing_section_plans",
        ["outline_node_id", "input_fingerprint"],
        unique=True,
        postgresql_where=sa.text("status = 'ready'"),
    )
    op.add_column(
        "writing_section_drafts",
        sa.Column("section_plan_id", sa.Integer(), nullable=True, comment="实际使用的章节论证计划ID"),
    )
    op.add_column(
        "writing_section_drafts",
        sa.Column("planning_mode", sa.String(length=32), nullable=True, comment="planned_new/planned_reused/legacy_*"),
    )
    op.add_column(
        "writing_section_drafts",
        sa.Column("planning_failure_code", sa.String(length=80), nullable=True, comment="规划降级失败码"),
    )
    op.create_index(
        "ix_writing_section_drafts_section_plan_id",
        "writing_section_drafts",
        ["section_plan_id"],
    )
    op.create_foreign_key(
        "fk_writing_section_drafts_section_plan_id",
        "writing_section_drafts",
        "writing_section_plans",
        ["section_plan_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_writing_section_drafts_section_plan_id",
        "writing_section_drafts",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_writing_section_drafts_section_plan_id",
        table_name="writing_section_drafts",
    )
    op.drop_column("writing_section_drafts", "planning_failure_code")
    op.drop_column("writing_section_drafts", "planning_mode")
    op.drop_column("writing_section_drafts", "section_plan_id")
    op.drop_index(
        "uq_writing_section_plans_ready_fingerprint",
        table_name="writing_section_plans",
    )
    op.drop_index(
        "uq_writing_section_plans_current_ready",
        table_name="writing_section_plans",
    )
    op.drop_index(
        "ix_writing_section_plans_outline_node_id",
        table_name="writing_section_plans",
    )
    op.drop_index(
        "ix_writing_section_plans_project_id",
        table_name="writing_section_plans",
    )
    op.drop_table("writing_section_plans")
