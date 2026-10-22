"""add knowledge base documents and chunks

Revision ID: e9f0a1b2c3d4
Revises: a3b4c5d6e7f8
Create Date: 2026-05-08 16:20:00.000000+08:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kb_documents",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("applicable_flows", sa.JSON(), nullable=False),
        sa.Column("business_type", sa.String(length=64), nullable=False),
        sa.Column("business_label", sa.String(length=100), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("authority", sa.String(length=32), nullable=False, server_default="user_experience"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.String(length=64), nullable=False, server_default="1"),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("owner_scope", sa.String(length=32), nullable=False, server_default="personal"),
        sa.Column("industry_tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("region_tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="approved"),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_status", sa.String(length=20), nullable=False, server_default="saved"),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["superseded_by_id"], ["kb_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_documents_owner_id", "kb_documents", ["owner_id"])
    op.create_index("ix_kb_documents_title", "kb_documents", ["title"])
    op.create_index("ix_kb_documents_business_type", "kb_documents", ["business_type"])
    op.create_index("ix_kb_documents_valid_to", "kb_documents", ["valid_to"])
    op.create_index("ix_kb_documents_authority", "kb_documents", ["authority"])
    op.create_index("ix_kb_documents_enabled", "kb_documents", ["enabled"])
    op.create_index("ix_kb_documents_superseded_by_id", "kb_documents", ["superseded_by_id"])
    op.create_index("ix_kb_documents_owner_scope", "kb_documents", ["owner_scope"])
    op.create_index("ix_kb_documents_review_status", "kb_documents", ["review_status"])
    op.create_index("ix_kb_documents_content_fingerprint", "kb_documents", ["content_fingerprint"])
    op.create_index("ix_kb_documents_processing_status", "kb_documents", ["processing_status"])

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("chunk_type", sa.String(length=32), nullable=False, server_default="text"),
        sa.Column("parent_block_id", sa.String(length=128), nullable=True),
        sa.Column("section_path", sa.Text(), nullable=True),
        sa.Column("chunk_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("embedding_version", sa.String(length=64), nullable=False, server_default="bge-m3-v1"),
        sa.Column("page_num", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["kb_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_fingerprint", name="uq_kb_chunks_document_fingerprint"),
    )
    op.create_index("ix_kb_chunks_document_id", "kb_chunks", ["document_id"])
    op.create_index("ix_kb_chunks_chunk_fingerprint", "kb_chunks", ["chunk_fingerprint"])

    op.add_column("writing_materials", sa.Column("kb_source_id", sa.Integer(), nullable=True))
    op.add_column("writing_materials", sa.Column("kb_source_version", sa.String(length=64), nullable=True))
    op.add_column("writing_materials", sa.Column("kb_snapshot_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("writing_materials", sa.Column("kb_source_chunk_ids", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_writing_materials_kb_source_id",
        "writing_materials",
        "kb_documents",
        ["kb_source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_writing_materials_kb_source_id", "writing_materials", ["kb_source_id"])


def downgrade() -> None:
    op.drop_index("ix_writing_materials_kb_source_id", table_name="writing_materials")
    op.drop_constraint("fk_writing_materials_kb_source_id", "writing_materials", type_="foreignkey")
    op.drop_column("writing_materials", "kb_source_chunk_ids")
    op.drop_column("writing_materials", "kb_snapshot_at")
    op.drop_column("writing_materials", "kb_source_version")
    op.drop_column("writing_materials", "kb_source_id")

    op.drop_index("ix_kb_chunks_chunk_fingerprint", table_name="kb_chunks")
    op.drop_index("ix_kb_chunks_document_id", table_name="kb_chunks")
    op.drop_table("kb_chunks")

    op.drop_index("ix_kb_documents_processing_status", table_name="kb_documents")
    op.drop_index("ix_kb_documents_content_fingerprint", table_name="kb_documents")
    op.drop_index("ix_kb_documents_review_status", table_name="kb_documents")
    op.drop_index("ix_kb_documents_owner_scope", table_name="kb_documents")
    op.drop_index("ix_kb_documents_superseded_by_id", table_name="kb_documents")
    op.drop_index("ix_kb_documents_enabled", table_name="kb_documents")
    op.drop_index("ix_kb_documents_authority", table_name="kb_documents")
    op.drop_index("ix_kb_documents_valid_to", table_name="kb_documents")
    op.drop_index("ix_kb_documents_business_type", table_name="kb_documents")
    op.drop_index("ix_kb_documents_title", table_name="kb_documents")
    op.drop_index("ix_kb_documents_owner_id", table_name="kb_documents")
    op.drop_table("kb_documents")
