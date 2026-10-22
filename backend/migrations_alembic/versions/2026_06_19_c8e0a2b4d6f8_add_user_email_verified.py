"""add_user_email_verified

Revision ID: c8e0a2b4d6f8
Revises: b7d9f1a3c5e7
Create Date: 2026-06-19

为 users 增加邮箱验证字段(反多账号硬拦截依据):
- email_verified: 是否已验证, 新用户默认 false。
- email_verified_at: 验证时间。

存量豁免(关键): 迁移时把所有已存在用户回填 email_verified=true,
保护现有(含已付费)用户不因开启硬拦截而被锁死; 仅新注册用户需验证。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e0a2b4d6f8"
down_revision: Union[str, None] = 'e4f6a8b0c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email_verified", sa.Boolean(), nullable=False,
            server_default=sa.false(), comment="邮箱是否已验证",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "email_verified_at", sa.DateTime(timezone=True), nullable=True,
            comment="邮箱验证时间",
        ),
    )
    # 存量豁免: 现有用户全部视为已验证, 避免开启硬拦截后被锁死
    op.execute("UPDATE users SET email_verified = true")


def downgrade() -> None:
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email_verified")
