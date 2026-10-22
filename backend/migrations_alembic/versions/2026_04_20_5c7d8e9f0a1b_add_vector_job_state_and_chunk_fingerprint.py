"""add vector job state and chunk fingerprint

Revision ID: 5c7d8e9f0a1b
Revises: ba47204e9080
Create Date: 2026-04-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5c7d8e9f0a1b"
down_revision: Union[str, None] = "ba47204e9080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("parent_block_id", sa.String(length=128), nullable=True))
    op.add_column("chunks", sa.Column("section_path", sa.Text(), nullable=True))
    op.add_column("chunks", sa.Column("chunk_fingerprint", sa.String(length=64), nullable=False, server_default="bootstrap"))
    op.add_column("chunks", sa.Column("embedding_version", sa.String(length=64), nullable=False, server_default="bge-m3-v1"))

    op.add_column("writing_material_chunks", sa.Column("chunk_fingerprint", sa.String(length=64), nullable=False, server_default="bootstrap"))
    op.add_column("writing_material_chunks", sa.Column("embedding_version", sa.String(length=64), nullable=False, server_default="bge-m3-v1"))
    op.add_column("writing_material_chunks", sa.Column("parent_block_id", sa.String(length=128), nullable=True))
    op.add_column("writing_material_chunks", sa.Column("section_path", sa.Text(), nullable=True))

    op.create_table(
        "vector_job_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.Enum("pending", "running", "completed", "failed", name="vectorjobstatus"), nullable=False, server_default="pending"),
        sa.Column("queue_mode", sa.String(length=32), nullable=False, server_default="asyncio"),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("progress", sa.String(length=128), nullable=True),
        sa.Column("degraded_to_cpu", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fallback_reason", sa.String(length=128), nullable=True),
        sa.Column("recovery_mode", sa.String(length=32), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_fingerprint_version", sa.String(length=64), nullable=False, server_default="bge-m3-v1"),
        sa.Column("shadow_summary", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", name="uq_vector_job_states_document_id"),
    )
    op.create_index("ix_vector_job_states_id", "vector_job_states", ["id"])
    op.create_index("ix_vector_job_states_project_id", "vector_job_states", ["project_id"])
    op.create_index("ix_vector_job_states_document_id", "vector_job_states", ["document_id"])

    op.execute(
        "UPDATE chunks "
        "SET chunk_fingerprint = md5(document_id::text || ':' || chunk_index::text || ':' || "
        "coalesce(page_num::text, '') || ':' || coalesce(content, '') || ':' || id::text) "
        "WHERE chunk_fingerprint = 'bootstrap'"
    )
    op.execute(
        "UPDATE writing_material_chunks "
        "SET chunk_fingerprint = md5(material_id::text || ':' || chunk_index::text || ':' || "
        "coalesce(page_num::text, '') || ':' || coalesce(content, '') || ':' || id::text) "
        "WHERE chunk_fingerprint = 'bootstrap'"
    )

    op.create_index("ix_chunks_chunk_fingerprint", "chunks", ["chunk_fingerprint"])
    op.create_unique_constraint("uq_chunks_document_fingerprint", "chunks", ["document_id", "chunk_fingerprint"])
    op.create_index(
        "ix_writing_material_chunks_chunk_fingerprint",
        "writing_material_chunks",
        ["chunk_fingerprint"],
    )
    op.create_unique_constraint(
        "uq_writing_material_chunks_material_fingerprint",
        "writing_material_chunks",
        ["material_id", "chunk_fingerprint"],
    )

    op.alter_column("chunks", "chunk_fingerprint", server_default=None)
    op.alter_column("writing_material_chunks", "chunk_fingerprint", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_vector_job_states_document_id", table_name="vector_job_states")
    op.drop_index("ix_vector_job_states_project_id", table_name="vector_job_states")
    op.drop_index("ix_vector_job_states_id", table_name="vector_job_states")
    op.drop_table("vector_job_states")

    op.drop_constraint("uq_writing_material_chunks_material_fingerprint", "writing_material_chunks", type_="unique")
    op.drop_index("ix_writing_material_chunks_chunk_fingerprint", table_name="writing_material_chunks")
    op.drop_column("writing_material_chunks", "section_path")
    op.drop_column("writing_material_chunks", "parent_block_id")
    op.drop_column("writing_material_chunks", "embedding_version")
    op.drop_column("writing_material_chunks", "chunk_fingerprint")

    op.drop_constraint("uq_chunks_document_fingerprint", "chunks", type_="unique")
    op.drop_index("ix_chunks_chunk_fingerprint", table_name="chunks")
    op.drop_column("chunks", "embedding_version")
    op.drop_column("chunks", "chunk_fingerprint")
    op.drop_column("chunks", "section_path")
    op.drop_column("chunks", "parent_block_id")

    op.execute("DROP TYPE IF EXISTS vectorjobstatus")
