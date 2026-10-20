"""create_project_milestone_events

Revision ID: c3e5f7a9b1d3
Revises: b2d4f6a8c0e1
Create Date: 2026-05-10

新增 project_milestone_events 表, 为项目级日志时间线提供 INFO 级业务里程碑数据源。

设计边界:
- 与 system_error_logs (WARNING+) 互补, 不污染错误日志表
- 与 task_observations 互补, 后者只承载 start/stage/end 事件元数据
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c3e5f7a9b1d3"
down_revision: Union[str, None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_milestone_events (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            incident_id VARCHAR(32),
            task_type VARCHAR(50),
            event_key VARCHAR(80) NOT NULL,
            summary VARCHAR(500),
            payload_json TEXT,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_milestone_events_project_id "
        "ON project_milestone_events (project_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_milestone_events_incident_id "
        "ON project_milestone_events (incident_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_milestone_events_task_type "
        "ON project_milestone_events (task_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_milestone_events_event_key "
        "ON project_milestone_events (event_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_milestone_events_occurred_at "
        "ON project_milestone_events (occurred_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_project_milestone_events_occurred_at")
    op.execute("DROP INDEX IF EXISTS ix_project_milestone_events_event_key")
    op.execute("DROP INDEX IF EXISTS ix_project_milestone_events_task_type")
    op.execute("DROP INDEX IF EXISTS ix_project_milestone_events_incident_id")
    op.execute("DROP INDEX IF EXISTS ix_project_milestone_events_project_id")
    op.execute("DROP TABLE IF EXISTS project_milestone_events")
