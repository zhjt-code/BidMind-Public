"""add_incident_root_cause_fields

Revision ID: b2d4f6a8c0e1
Revises: a1c2d3e4f5b6
Create Date: 2026-05-10

为 system_error_logs 增加错误链路与根因定位相关字段:
- incident_id: 同一次失败链路的关联键
- chain_seq: 链路内顺序
- is_root_cause: 根因标记
- exception_type: 异常类型
- origin_module / origin_function / origin_line: traceback 末端帧坐标

旧数据保持 NULL/False, 后续新写入由 log_sink 自动填充。
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, None] = "a1c2d3e4f5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE system_error_logs "
        "ADD COLUMN IF NOT EXISTS incident_id VARCHAR(32)"
    )
    op.execute(
        "ALTER TABLE system_error_logs "
        "ADD COLUMN IF NOT EXISTS chain_seq INTEGER"
    )
    op.execute(
        "ALTER TABLE system_error_logs "
        "ADD COLUMN IF NOT EXISTS is_root_cause BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE system_error_logs "
        "ADD COLUMN IF NOT EXISTS exception_type VARCHAR(128)"
    )
    op.execute(
        "ALTER TABLE system_error_logs "
        "ADD COLUMN IF NOT EXISTS origin_module VARCHAR(255)"
    )
    op.execute(
        "ALTER TABLE system_error_logs "
        "ADD COLUMN IF NOT EXISTS origin_function VARCHAR(255)"
    )
    op.execute(
        "ALTER TABLE system_error_logs "
        "ADD COLUMN IF NOT EXISTS origin_line INTEGER"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_system_error_logs_incident_id "
        "ON system_error_logs (incident_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_system_error_logs_is_root_cause "
        "ON system_error_logs (is_root_cause)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_system_error_logs_exception_type "
        "ON system_error_logs (exception_type)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_system_error_logs_exception_type")
    op.execute("DROP INDEX IF EXISTS ix_system_error_logs_is_root_cause")
    op.execute("DROP INDEX IF EXISTS ix_system_error_logs_incident_id")

    op.execute("ALTER TABLE system_error_logs DROP COLUMN IF EXISTS origin_line")
    op.execute("ALTER TABLE system_error_logs DROP COLUMN IF EXISTS origin_function")
    op.execute("ALTER TABLE system_error_logs DROP COLUMN IF EXISTS origin_module")
    op.execute("ALTER TABLE system_error_logs DROP COLUMN IF EXISTS exception_type")
    op.execute("ALTER TABLE system_error_logs DROP COLUMN IF EXISTS is_root_cause")
    op.execute("ALTER TABLE system_error_logs DROP COLUMN IF EXISTS chain_seq")
    op.execute("ALTER TABLE system_error_logs DROP COLUMN IF EXISTS incident_id")
