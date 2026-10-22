"""add knowledge base atoms

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-05-09 10:10:00.000000+08:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kb_atoms",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("business_type", sa.String(length=64), nullable=False),
        sa.Column("business_label", sa.String(length=100), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("atom_type", sa.String(length=32), nullable=False, server_default="page"),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("source_chunk_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_image_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("processing_status", sa.String(length=20), nullable=False, server_default="completed"),
        sa.ForeignKeyConstraint(["document_id"], ["kb_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "fingerprint", name="uq_kb_atoms_document_fingerprint"),
    )
    op.create_index("ix_kb_atoms_document_id", "kb_atoms", ["document_id"])
    op.create_index("ix_kb_atoms_business_type", "kb_atoms", ["business_type"])
    op.create_index("ix_kb_atoms_fingerprint", "kb_atoms", ["fingerprint"])
    op.create_index("ix_kb_atoms_processing_status", "kb_atoms", ["processing_status"])

    op.create_table(
        "kb_atom_chunks",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atom_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("chunk_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("embedding_version", sa.String(length=64), nullable=False, server_default="bge-m3-v1"),
        sa.Column("page_num", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["atom_id"], ["kb_atoms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["kb_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("atom_id", "chunk_fingerprint", name="uq_kb_atom_chunks_atom_fingerprint"),
    )
    op.create_index("ix_kb_atom_chunks_atom_id", "kb_atom_chunks", ["atom_id"])
    op.create_index("ix_kb_atom_chunks_document_id", "kb_atom_chunks", ["document_id"])
    op.create_index("ix_kb_atom_chunks_chunk_fingerprint", "kb_atom_chunks", ["chunk_fingerprint"])

    op.add_column("writing_materials", sa.Column("kb_atom_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_writing_materials_kb_atom_id",
        "writing_materials",
        "kb_atoms",
        ["kb_atom_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_writing_materials_kb_atom_id", "writing_materials", ["kb_atom_id"])


def downgrade() -> None:
    op.drop_index("ix_writing_materials_kb_atom_id", table_name="writing_materials")
    op.drop_constraint("fk_writing_materials_kb_atom_id", "writing_materials", type_="foreignkey")
    op.drop_column("writing_materials", "kb_atom_id")

    op.drop_index("ix_kb_atom_chunks_chunk_fingerprint", table_name="kb_atom_chunks")
    op.drop_index("ix_kb_atom_chunks_document_id", table_name="kb_atom_chunks")
    op.drop_index("ix_kb_atom_chunks_atom_id", table_name="kb_atom_chunks")
    op.drop_table("kb_atom_chunks")

    op.drop_index("ix_kb_atoms_processing_status", table_name="kb_atoms")
    op.drop_index("ix_kb_atoms_fingerprint", table_name="kb_atoms")
    op.drop_index("ix_kb_atoms_business_type", table_name="kb_atoms")
    op.drop_index("ix_kb_atoms_document_id", table_name="kb_atoms")
    op.drop_table("kb_atoms")
