"""add_message_fingerprint

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-04-10

为 system_error_logs 添加 message_fingerprint 列并回填历史数据。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BATCH_SIZE = 500


def _backfill_fingerprints(connection: sa.engine.Connection) -> None:
    """分批回填历史数据的 message_fingerprint。"""
    from app.core.message_fingerprint import compute_fingerprint

    meta = sa.MetaData()
    table = sa.Table(
        "system_error_logs", meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("module", sa.String),
        sa.Column("level", sa.String),
        sa.Column("message", sa.Text),
        sa.Column("message_fingerprint", sa.String(32)),
    )

    while True:
        rows = connection.execute(
            sa.select(table.c.id, table.c.module, table.c.level, table.c.message)
            .where(table.c.message_fingerprint.is_(None))
            .limit(BATCH_SIZE)
        ).fetchall()

        if not rows:
            break

        for row in rows:
            fp = compute_fingerprint(row.module, row.level, row.message)
            connection.execute(
                table.update()
                .where(table.c.id == row.id)
                .values(message_fingerprint=fp)
            )
        connection.commit()


def upgrade() -> None:
    op.execute(
        "ALTER TABLE system_error_logs "
        "ADD COLUMN IF NOT EXISTS message_fingerprint VARCHAR(32)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_system_error_logs_message_fingerprint "
        "ON system_error_logs (message_fingerprint)"
    )

    connection = op.get_bind()
    _backfill_fingerprints(connection)


def downgrade() -> None:
    op.drop_index(
        op.f("ix_system_error_logs_message_fingerprint"),
        table_name="system_error_logs",
    )
    op.drop_column("system_error_logs", "message_fingerprint")
