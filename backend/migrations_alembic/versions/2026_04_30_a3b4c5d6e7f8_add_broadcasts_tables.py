"""add_broadcasts_tables

新增站内信广播能力：
1. broadcasts 表：管理员发布的广播主体（一条广播一行）
2. broadcast_user_states 表：用户对广播的"已读 / 已关闭"状态

Revision ID: a3b4c5d6e7f8
Revises: c7d8e9f0a1b2
Create Date: 2026-04-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) broadcasts 主表
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "level",
            sa.Enum(
                "INFO", "SUCCESS", "WARNING", "CRITICAL",
                name="broadcast_level_enum", create_type=True,
            ),
            nullable=False,
            server_default="INFO",
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("ref_url", sa.String(500), nullable=True),
        sa.Column("show_banner", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("dismissible", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_index("ix_broadcasts_starts_at", "broadcasts", ["starts_at"])
    op.create_index("ix_broadcasts_ends_at", "broadcasts", ["ends_at"])
    op.create_index("ix_broadcasts_deleted_at", "broadcasts", ["deleted_at"])
    # 复合索引支持"当前生效"窗口查询
    op.create_index(
        "ix_broadcasts_active_window",
        "broadcasts",
        ["deleted_at", "starts_at", "ends_at"],
    )

    # 2) broadcast_user_states 状态表
    op.create_table(
        "broadcast_user_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "broadcast_id", sa.Integer(),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_index("ix_broadcast_user_states_user_id", "broadcast_user_states", ["user_id"])
    op.create_index("ix_broadcast_user_states_broadcast_id", "broadcast_user_states", ["broadcast_id"])
    op.create_unique_constraint(
        "uq_broadcast_user_state",
        "broadcast_user_states",
        ["user_id", "broadcast_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_broadcast_user_state", "broadcast_user_states", type_="unique"
    )
    op.drop_index("ix_broadcast_user_states_broadcast_id", table_name="broadcast_user_states")
    op.drop_index("ix_broadcast_user_states_user_id", table_name="broadcast_user_states")
    op.drop_table("broadcast_user_states")

    op.drop_index("ix_broadcasts_active_window", table_name="broadcasts")
    op.drop_index("ix_broadcasts_deleted_at", table_name="broadcasts")
    op.drop_index("ix_broadcasts_ends_at", table_name="broadcasts")
    op.drop_index("ix_broadcasts_starts_at", table_name="broadcasts")
    op.drop_table("broadcasts")
    op.execute("DROP TYPE IF EXISTS broadcast_level_enum")
