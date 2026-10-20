"""add project log snapshot columns and relax observability FKs

Revision ID: b9d4e1f6a2c8
Revises: e0a2c4e6f8b0
Create Date: 2026-06-23

为运维监控项目日志追踪做数据层准备:
1. system_error_logs / task_observations / project_milestone_events 增加
   project_name + owner_username 快照列 (项目删除前回填, 已删项目仍可按名显示/搜索)
2. 把 task_observations / project_milestone_events 的 project_id 外键移除,
   与 system_error_logs 统一为"无外键纯整数列": 项目物理删除后三表 project_id
   全部保留, 可按 id 聚合时间线 (否则 SET NULL/CASCADE 会让已删项目聚合不起来)
3. project_milestone_events.project_id 放开 NOT NULL (删除后保留为孤儿 id)
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b9d4e1f6a2c8"
down_revision: Union[str, None] = "e0a2c4e6f8b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 三张可观测表统一的快照列 (project_name 255 / owner_username 50)
_SNAPSHOT_TABLES = ("system_error_logs", "task_observations", "project_milestone_events")

# 需要移除指向 projects 外键的表 (system_error_logs 本就无外键)
_FK_TABLES = ("task_observations", "project_milestone_events")


def _drop_project_fks(table: str) -> None:
    """删除 table 上所有外键约束 (这两张表仅有 project_id 一个外键)。

    用 pg_constraint 动态查名, 不依赖 PG 默认命名, 避免约束名不一致导致静默漏删。
    """
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = '{table}'::regclass AND contype = 'f'
            LOOP
                EXECUTE 'ALTER TABLE {table} DROP CONSTRAINT ' || quote_ident(r.conname);
            END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    # 1. 三表增加快照列 + 索引 (幂等)
    for table in _SNAPSHOT_TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS project_name VARCHAR(255)"
        )
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS owner_username VARCHAR(50)"
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_project_name "
            f"ON {table} (project_name)"
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_owner_username "
            f"ON {table} (owner_username)"
        )

    # 2. 移除 task_observations / project_milestone_events 指向 projects 的外键
    for table in _FK_TABLES:
        _drop_project_fks(table)

    # 3. project_milestone_events.project_id 放开 NOT NULL
    op.execute(
        "ALTER TABLE project_milestone_events ALTER COLUMN project_id DROP NOT NULL"
    )


def downgrade() -> None:
    # 反向 3: 恢复 NOT NULL 前清理孤儿 NULL 行 (回滚场景, 删除已与项目脱钩的里程碑)
    op.execute(
        "DELETE FROM project_milestone_events WHERE project_id IS NULL"
    )
    op.execute(
        "ALTER TABLE project_milestone_events ALTER COLUMN project_id SET NOT NULL"
    )

    # 反向 2: 恢复外键 (task_observations=SET NULL, milestone=CASCADE, 与原定义一致)
    op.execute(
        "ALTER TABLE task_observations "
        "ADD CONSTRAINT task_observations_project_id_fkey "
        "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE project_milestone_events "
        "ADD CONSTRAINT project_milestone_events_project_id_fkey "
        "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE"
    )

    # 反向 1: 删除快照列与索引
    for table in _SNAPSHOT_TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_owner_username")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_project_name")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS owner_username")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS project_name")
