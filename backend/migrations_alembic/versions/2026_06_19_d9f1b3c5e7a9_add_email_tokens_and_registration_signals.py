"""add_email_tokens_and_registration_signals

Revision ID: d9f1b3c5e7a9
Revises: c8e0a2b4d6f8
Create Date: 2026-06-19

新增反多账号支撑表:
- email_verification_tokens: 一次性邮箱验证令牌(存哈希)+有效期+使用状态。
- registration_signals: 注册时设备指纹哈希与来源 IP, 供设备指纹软上限统计。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9f1b3c5e7a9"
down_revision: Union[str, None] = "c8e0a2b4d6f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_verification_tokens",
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
    op.create_index(op.f("ix_email_verification_tokens_id"), "email_verification_tokens", ["id"], unique=False)
    op.create_index(op.f("ix_email_verification_tokens_user_id"), "email_verification_tokens", ["user_id"], unique=False)
    op.create_index(op.f("ix_email_verification_tokens_token_hash"), "email_verification_tokens", ["token_hash"], unique=True)

    op.create_table(
        "registration_signals",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="所属用户"),
        sa.Column("fingerprint_hash", sa.String(length=64), nullable=True, comment="设备指纹哈希"),
        sa.Column("ip_address", sa.String(length=64), nullable=True, comment="注册来源 IP"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_registration_signals_id"), "registration_signals", ["id"], unique=False)
    op.create_index(op.f("ix_registration_signals_user_id"), "registration_signals", ["user_id"], unique=False)
    op.create_index(op.f("ix_registration_signals_fingerprint_hash"), "registration_signals", ["fingerprint_hash"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_registration_signals_fingerprint_hash"), table_name="registration_signals")
    op.drop_index(op.f("ix_registration_signals_user_id"), table_name="registration_signals")
    op.drop_index(op.f("ix_registration_signals_id"), table_name="registration_signals")
    op.drop_table("registration_signals")

    op.drop_index(op.f("ix_email_verification_tokens_token_hash"), table_name="email_verification_tokens")
    op.drop_index(op.f("ix_email_verification_tokens_user_id"), table_name="email_verification_tokens")
    op.drop_index(op.f("ix_email_verification_tokens_id"), table_name="email_verification_tokens")
    op.drop_table("email_verification_tokens")
