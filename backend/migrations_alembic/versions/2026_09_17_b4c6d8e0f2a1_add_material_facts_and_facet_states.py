"""add_material_facts_and_facet_states: 文档级材料事实库存 + 投标响应分面闭合状态。

Revision ID: b4c6d8e0f2a1
Revises: 0835e9285d94
Create Date: 2026-09-17 09:40:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c6d8e0f2a1"
down_revision: Union[str, None] = "0835e9285d94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "material_facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("canonical_material_key", sa.String(length=255), nullable=False),
        sa.Column("raw_noun", sa.String(length=255), nullable=False),
        sa.Column("grade", sa.String(length=16), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("bid_part", sa.String(length=16), nullable=True),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("physical_page", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("identifier", sa.String(length=128), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("source_channel", sa.String(length=32), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "canonical_material_key",
            "document_id",
            "chunk_id",
            "physical_page",
            name="uq_material_facts_identity",
        ),
    )
    op.create_index(
        op.f("ix_material_facts_id"), "material_facts", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_material_facts_project_id"),
        "material_facts",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_material_facts_canonical_material_key"),
        "material_facts",
        ["canonical_material_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_material_facts_document_id"),
        "material_facts",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_material_facts_chunk_id"),
        "material_facts",
        ["chunk_id"],
        unique=False,
    )
    op.add_column(
        "bid_responses",
        sa.Column("facet_states", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bid_responses", "facet_states")
    op.drop_index(op.f("ix_material_facts_chunk_id"), table_name="material_facts")
    op.drop_index(op.f("ix_material_facts_document_id"), table_name="material_facts")
    op.drop_index(
        op.f("ix_material_facts_canonical_material_key"),
        table_name="material_facts",
    )
    op.drop_index(op.f("ix_material_facts_project_id"), table_name="material_facts")
    op.drop_index(op.f("ix_material_facts_id"), table_name="material_facts")
    op.drop_table("material_facts")
