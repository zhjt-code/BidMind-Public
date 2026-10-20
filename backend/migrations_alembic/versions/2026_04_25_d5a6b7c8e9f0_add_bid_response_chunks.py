"""add_bid_response_chunks

Fix-02 / 问题 2：新增 bid_response_chunks 表，持久化每条投标响应的多路检索命中，
作为下游审查 Gate（PageAnchorVerifyGate 等）的事实参照源，消除
"审查 Gate 证据基座依赖 LLM 自产字段" 的结构性风险。

Revision ID: d5a6b7c8e9f0
Revises: d5e6f7a8b9c0
Create Date: 2026-04-25 09:00:00.000000+08:00

Note: 本迁移最初使用 revision=f6a7b8c9d0e1，与 2026-03-27
``add_clause_embedding`` 发生撞车导致 alembic 拒绝解析（CycleDetected）。
修复时改为唯一 id d5a6b7c8e9f0，下游迁移 b2f7d8e9a0c1 同步更新。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5a6b7c8e9f0"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bid_response_chunks",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("bid_response_id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("page_num", sa.Integer(), nullable=True),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("matched_sub_query", sa.Text(), nullable=True),
        sa.Column("matched_keywords", sa.JSON(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["bid_response_id"], ["bid_responses.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bid_response_chunks_bid_response_id",
        "bid_response_chunks",
        ["bid_response_id"],
    )
    op.create_index(
        "ix_bid_response_chunks_chunk_id",
        "bid_response_chunks",
        ["chunk_id"],
    )
    op.create_index(
        "ix_bid_response_chunks_document_id",
        "bid_response_chunks",
        ["document_id"],
    )
    op.create_index(
        "ix_bid_response_chunks_role",
        "bid_response_chunks",
        ["role"],
    )
    op.create_index(
        "ix_bid_response_chunks_doc_page",
        "bid_response_chunks",
        ["document_id", "page_num"],
    )
    op.create_index(
        "ix_bid_response_chunks_resp_role",
        "bid_response_chunks",
        ["bid_response_id", "role"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bid_response_chunks_resp_role", table_name="bid_response_chunks",
    )
    op.drop_index(
        "ix_bid_response_chunks_doc_page", table_name="bid_response_chunks",
    )
    op.drop_index("ix_bid_response_chunks_role", table_name="bid_response_chunks")
    op.drop_index(
        "ix_bid_response_chunks_document_id", table_name="bid_response_chunks",
    )
    op.drop_index("ix_bid_response_chunks_chunk_id", table_name="bid_response_chunks")
    op.drop_index(
        "ix_bid_response_chunks_bid_response_id", table_name="bid_response_chunks",
    )
    op.drop_table("bid_response_chunks")
