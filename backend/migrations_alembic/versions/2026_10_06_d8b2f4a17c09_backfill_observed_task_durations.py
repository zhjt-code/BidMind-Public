"""backfill observed success spans into task runs

没有完成运行记录的成功观测（串标正常分析、未建重试记录的审查）补成冻结耗时。
已有完成运行覆盖该时段的不重复插入。

Revision ID: d8b2f4a17c09
Revises: c3a8e1d94b27
Create Date: 2026-10-06 14:00:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = "d8b2f4a17c09"
down_revision: Union[str, None] = "c3a8e1d94b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO task_runs (
            project_id, flow, node_key, task_name, status, attempt,
            duration_ms, created_at, updated_at
        )
        SELECT
            t.project_id,
            left(t.flow::text, 64),
            left('observed:' || t.task_type || ':' || t.id::text, 128),
            left(COALESCE(t.task_id, 'observed:' || t.id::text), 128),
            'completed',
            1,
            t.duration_ms,
            COALESCE(t.started_at, t.created_at),
            COALESCE(t.ended_at, t.started_at, t.created_at)
        FROM task_observations t
        WHERE t.phase::text = 'end'
          AND t.outcome::text = 'success'
          AND t.project_id IS NOT NULL
          AND t.duration_ms > 0
          AND EXISTS (SELECT 1 FROM projects p WHERE p.id = t.project_id)
          AND NOT EXISTS (
              SELECT 1
              FROM task_runs r
              WHERE r.project_id = t.project_id
                AND r.status = 'completed'
                AND r.duration_ms > 0
                AND r.created_at <= COALESCE(t.ended_at, t.created_at)
                AND r.updated_at >= COALESCE(t.started_at, t.created_at)
          )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM task_runs WHERE node_key LIKE 'observed:%'")
