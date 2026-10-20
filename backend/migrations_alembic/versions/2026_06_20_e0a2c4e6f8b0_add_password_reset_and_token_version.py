"""add_password_reset_and_token_version

Revision ID: e0a2c4e6f8b0
Revises: d9f1b3c5e7a9
Create Date: 2026-06-20

新增自助"忘记密码"支撑:
- users.token_version: 令牌版本(默认0); 重置密码/管理员重置时自增, 令该用户旧 JWT 立即失效。
  存量用户默认0; 旧令牌无 ver 视为0, 与默认一致, 部署不触发掉线。
- password_reset_tokens: 一次性密码重置令牌(存哈希)+有效期+使用状态。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e0a2c4e6f8b0"
down_revision: Union[str, None] = "d9f1b3c5e7a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version", sa.Integer(), nullable=False, server_default="0",
            comment="令牌版本: 重置密码/管理员重置时自增, 令旧 JWT 失效",
        ),
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="所属用户"),
        sa.Column("token_hash", sa.String(length=64), nullable=False, comment="sha256(原始令牌)"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment="过期时间"),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True, comment="使用时间"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_password_reset_tokens_id"), "password_reset_tokens", ["id"], unique=False)
    op.create_index(op.f("ix_password_reset_tokens_user_id"), "password_reset_tokens", ["user_id"], unique=False)
    op.create_index(op.f("ix_password_reset_tokens_token_hash"), "password_reset_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_password_reset_tokens_token_hash"), table_name="password_reset_tokens")
    op.drop_index(op.f("ix_password_reset_tokens_user_id"), table_name="password_reset_tokens")
    op.drop_index(op.f("ix_password_reset_tokens_id"), table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_column("users", "token_version")
