"""add_writing_section_diagrams

Revision ID: d2f4a6b8c0e1
Revises: c1e3f5a7b9d2
Create Date: 2026-07-22 21:20:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d2f4a6b8c0e1"
down_revision: Union[str, None] = "c1e3f5a7b9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "writing_section_diagrams",
        sa.Column("project_id", sa.Integer(), nullable=False, comment="所属写标项目ID"),
        sa.Column("outline_node_id", sa.Integer(), nullable=False, comment="所属目录节点ID"),
        sa.Column("section_plan_id", sa.Integer(), nullable=False, comment="来源章节论证计划ID"),
        sa.Column("visual_id", sa.String(length=64), nullable=False, comment="VisualIntent标识"),
        sa.Column("diagram_type", sa.String(length=20), nullable=False, comment="flowchart/gantt"),
        sa.Column("title", sa.String(length=200), nullable=False, comment="图题"),
        sa.Column("ir_json", postgresql.JSONB(), nullable=True, comment="Diagram IR"),
        sa.Column("png_path", sa.String(length=500), nullable=True, comment="渲染PNG路径"),
        sa.Column("content_sha256", sa.String(length=64), nullable=True, comment="IR内容哈希"),
        sa.Column("status", sa.String(length=20), nullable=False, comment="pending/ready/failed"),
        sa.Column("failure_code", sa.String(length=80), nullable=True, comment="失败码"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'ready', 'failed')",
            name="ck_writing_section_diagrams_status",
        ),
        sa.CheckConstraint(
            "diagram_type in ('flowchart', 'gantt')",
            name="ck_writing_section_diagrams_type",
        ),
        sa.ForeignKeyConstraint(["outline_node_id"], ["writing_outline_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["section_plan_id"], ["writing_section_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "section_plan_id",
            "visual_id",
            name="uq_writing_section_diagrams_plan_visual",
        ),
    )
    op.create_index(
        "ix_writing_section_diagrams_project_id",
        "writing_section_diagrams",
        ["project_id"],
    )
    op.create_index(
        "ix_writing_section_diagrams_outline_node_id",
        "writing_section_diagrams",
        ["outline_node_id"],
    )
    op.create_index(
        "ix_writing_section_diagrams_section_plan_id",
        "writing_section_diagrams",
        ["section_plan_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_writing_section_diagrams_section_plan_id",
        table_name="writing_section_diagrams",
    )
    op.drop_index(
        "ix_writing_section_diagrams_outline_node_id",
        table_name="writing_section_diagrams",
    )
    op.drop_index(
        "ix_writing_section_diagrams_project_id",
        table_name="writing_section_diagrams",
    )
    op.drop_table("writing_section_diagrams")
