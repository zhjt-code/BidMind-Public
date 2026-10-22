"""add_tender_chunk_extraction_cache

Revision ID: a9c1e3f5b7d9
Revises: f4a6b8c0d2e4
Create Date: 2026-07-20 10:20:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a9c1e3f5b7d9"
down_revision: Union[str, None] = "f4a6b8c0d2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_chunk_extraction_cache",
        sa.Column("document_id", sa.Integer(), nullable=False, comment="所属招标文档ID"),
        sa.Column("chunk_id", sa.Integer(), nullable=False, comment="来源分块ID"),
        sa.Column("chunk_fingerprint", sa.String(length=64), nullable=False, comment="分块幂等指纹"),
        sa.Column("prompt_signature", sa.String(length=64), nullable=False, comment="system/user prompt 内容签名"),
        sa.Column("model_id", sa.String(length=200), nullable=False, comment="LLM模型标识"),
        sa.Column("extractor_schema_version", sa.String(length=32), nullable=False, comment="抽取缓存结构版本"),
        sa.Column("section_context_enabled", sa.Boolean(), nullable=False, comment="缓存生成时是否启用章节上下文注入"),
        sa.Column("result_items", postgresql.JSONB(), nullable=False, comment="ExtractedRequirement.model_dump() 列表"),
        sa.Column("empty_result", sa.Boolean(), nullable=False, comment="本 chunk 是否为合法空结果"),
        sa.Column("failure_fingerprint", sa.Text(), nullable=True, comment="失败摘要指纹(观测用, 不代表可复用结果)"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_fingerprint",
            "prompt_signature",
            "model_id",
            "extractor_schema_version",
            "section_context_enabled",
            name="uq_tender_chunk_extract_cache_key",
        ),
    )
    op.create_index(
        "ix_tender_chunk_extraction_cache_chunk_fingerprint",
        "tender_chunk_extraction_cache",
        ["chunk_fingerprint"],
    )
    op.create_index(
        "ix_tender_chunk_extraction_cache_chunk_id",
        "tender_chunk_extraction_cache",
        ["chunk_id"],
    )
    op.create_index(
        "ix_tender_chunk_extraction_cache_document_id",
        "tender_chunk_extraction_cache",
        ["document_id"],
    )
    op.create_index(
        "ix_tender_chunk_extraction_cache_prompt_signature",
        "tender_chunk_extraction_cache",
        ["prompt_signature"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tender_chunk_extraction_cache_prompt_signature",
        table_name="tender_chunk_extraction_cache",
    )
    op.drop_index(
        "ix_tender_chunk_extraction_cache_document_id",
        table_name="tender_chunk_extraction_cache",
    )
    op.drop_index(
        "ix_tender_chunk_extraction_cache_chunk_id",
        table_name="tender_chunk_extraction_cache",
    )
    op.drop_index(
        "ix_tender_chunk_extraction_cache_chunk_fingerprint",
        table_name="tender_chunk_extraction_cache",
    )
    op.drop_table("tender_chunk_extraction_cache")
