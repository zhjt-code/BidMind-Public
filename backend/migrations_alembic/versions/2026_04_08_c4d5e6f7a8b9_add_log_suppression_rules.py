"""add_log_suppression_rules

Revision ID: c4d5e6f7a8b9
Revises: f1e2d3c4b5a6
Create Date: 2026-04-08

新增日志抑制规则表，管理员可从错误日志创建规则，
后续同类日志在 Sink 层拦截不入库:
- log_suppression_rules
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS log_suppression_rules (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            module VARCHAR(255) NOT NULL,
            function_name VARCHAR(255),
            message_prefix VARCHAR(200) NOT NULL,
            level VARCHAR(20),
            category VARCHAR(30),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            reason TEXT,
            created_by VARCHAR(100) NOT NULL,
            source_log_id INTEGER,
            suppressed_count BIGINT NOT NULL DEFAULT 0,
            last_suppressed_at TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_log_suppression_rules_module "
        "ON log_suppression_rules (module)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_log_suppression_rules_enabled "
        "ON log_suppression_rules (enabled)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS log_suppression_rules")
