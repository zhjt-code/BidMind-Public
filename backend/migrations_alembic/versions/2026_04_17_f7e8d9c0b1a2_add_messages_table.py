"""add_messages_table

Revision ID: f7e8d9c0b1a2
Revises: 5e7f8a9b0c1d
Create Date: 2026-04-17

站内信表，用于管理员回复反馈时通知用户。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f7e8d9c0b1a2"
down_revision: Union[str, None] = "5e7f8a9b0c1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "type",
            sa.Enum("FEEDBACK_REPLY", "SYSTEM", name="message_type_enum", create_type=True),
            nullable=False,
            server_default="SYSTEM",
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ref_type", sa.String(50), nullable=True),
        sa.Column("ref_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_index("ix_messages_user_id", "messages", ["user_id"])
    op.create_index("ix_messages_is_read", "messages", ["is_read"])
    op.create_index(
        "ix_messages_user_unread",
        "messages",
        ["user_id", "is_read", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_user_unread", table_name="messages")
    op.drop_index("ix_messages_is_read", table_name="messages")
    op.drop_index("ix_messages_user_id", table_name="messages")
    op.drop_table("messages")
    op.execute("DROP TYPE IF EXISTS message_type_enum")
