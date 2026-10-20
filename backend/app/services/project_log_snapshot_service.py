"""项目日志快照回填。

职责: 项目物理删除前, 把项目名与属主用户名回填到三张可观测日志表的快照列
不做: 不写入日志、不查询时间线、不做日志清理 (各由对应模块负责)
"""

from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 与迁移 b9d4e1f6a2c8 对齐: 三表统一拥有 project_name/owner_username 快照列
_SNAPSHOT_TABLES = (
    "system_error_logs",
    "task_observations",
    "project_milestone_events",
)


async def backfill_project_log_snapshot(
    db: AsyncSession,
    *,
    project_id: int,
    project_name: Optional[str],
    owner_username: Optional[str],
) -> None:
    """把项目名/属主用户名回填到三表中该项目的日志行。

    在项目物理删除前调用, 使已删项目的日志在运维监控中仍可按名显示/搜索。
    幂等: 覆盖写, 可重复执行。调用方负责 flush/commit。

    表名为模块内固定常量 (非外部输入), f-string 拼接安全。
    """
    for table in _SNAPSHOT_TABLES:
        await db.execute(
            text(
                f"UPDATE {table} "
                f"SET project_name = :pname, owner_username = :owner "
                f"WHERE project_id = :pid"
            ),
            {"pname": project_name, "owner": owner_username, "pid": project_id},
        )
