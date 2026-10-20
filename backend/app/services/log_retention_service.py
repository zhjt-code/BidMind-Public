"""可观测日志保留清理 (阶段二)。

职责: 按差异化 TTL 分批删除过期的错误日志/任务观测/里程碑, 控制云端存储占用
不做: 不删业务数据、不写日志、不查询时间线

差异化保留 (针对"用户延后反馈"痛点):
- ERROR/CRITICAL 错误链路久存 (默认 30 天, 覆盖延后反馈窗口)
- WARNING / 任务观测 / 里程碑 短存 (默认 7 天)
分批删除 (id IN 子查询 + 每批提交) 避免长事务锁表。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_attempt_event import LlmAttemptEvent
from app.models.project_milestone_event import ProjectMilestoneEvent
from app.models.system_error_log import SystemErrorLog
from app.models.task_observation import TaskObservation

DEFAULT_ERROR_RETENTION_DAYS = 30
DEFAULT_NORMAL_RETENTION_DAYS = 7
DEFAULT_ATTEMPT_RETENTION_DAYS = 30
_DEFAULT_BATCH_SIZE = 1000
_ERROR_LEVELS = ("ERROR", "CRITICAL")


async def _query_expired_ids(
    db: AsyncSession,
    *,
    model: Any,
    ts_column: ColumnElement,
    cutoff: datetime,
    extra_filter: Optional[ColumnElement] = None,
    limit: int,
) -> list[int]:
    """只读: 返回 ts_column < cutoff (且满足 extra_filter) 的行 id, 最多 limit 条。

    抽成独立纯查询便于防御测试验证"差异化保留"的边界, 不产生删除副作用。
    """
    query = select(model.id).where(ts_column < cutoff)
    if extra_filter is not None:
        query = query.where(extra_filter)
    query = query.limit(limit)
    return list((await db.execute(query)).scalars().all())


async def _batch_delete(
    db: AsyncSession,
    *,
    model: Any,
    ts_column: ColumnElement,
    cutoff: datetime,
    extra_filter: Optional[ColumnElement] = None,
    batch_size: int,
) -> int:
    """分批删除过期行, 每批提交, 返回删除总数。"""
    total = 0
    while True:
        ids = await _query_expired_ids(
            db, model=model, ts_column=ts_column, cutoff=cutoff,
            extra_filter=extra_filter, limit=batch_size,
        )
        if not ids:
            break
        await db.execute(delete(model).where(model.id.in_(ids)))
        await db.commit()
        total += len(ids)
        if len(ids) < batch_size:
            break
    return total


async def prune_observability_logs(
    db: AsyncSession,
    *,
    error_retention_days: int = DEFAULT_ERROR_RETENTION_DAYS,
    normal_retention_days: int = DEFAULT_NORMAL_RETENTION_DAYS,
    attempt_retention_days: int = DEFAULT_ATTEMPT_RETENTION_DAYS,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    now: Optional[datetime] = None,
) -> dict[str, int]:
    """差异化 TTL 清理可观测表, 返回各类删除计数。

    Args:
        error_retention_days: ERROR/CRITICAL 保留天数 (久存)
        normal_retention_days: WARNING/观测/里程碑 保留天数 (短存)
        attempt_retention_days: LLM attempt 事实记录保留天数 (P0-A, 默认 30 天)
        now: 参考时间 (测试可注入); 默认当前 UTC
    """
    ref_now = now or datetime.now(timezone.utc)
    error_cutoff = ref_now - timedelta(days=error_retention_days)
    normal_cutoff = ref_now - timedelta(days=normal_retention_days)
    attempt_cutoff = ref_now - timedelta(days=attempt_retention_days)

    error_logs_error = await _batch_delete(
        db, model=SystemErrorLog, ts_column=SystemErrorLog.created_at,
        cutoff=error_cutoff, extra_filter=SystemErrorLog.level.in_(_ERROR_LEVELS),
        batch_size=batch_size,
    )
    error_logs_warning = await _batch_delete(
        db, model=SystemErrorLog, ts_column=SystemErrorLog.created_at,
        cutoff=normal_cutoff, extra_filter=SystemErrorLog.level == "WARNING",
        batch_size=batch_size,
    )
    task_observations = await _batch_delete(
        db, model=TaskObservation, ts_column=TaskObservation.created_at,
        cutoff=normal_cutoff, batch_size=batch_size,
    )
    milestones = await _batch_delete(
        db, model=ProjectMilestoneEvent, ts_column=ProjectMilestoneEvent.occurred_at,
        cutoff=normal_cutoff, batch_size=batch_size,
    )
    llm_attempts = await _batch_delete(
        db, model=LlmAttemptEvent, ts_column=LlmAttemptEvent.occurred_at,
        cutoff=attempt_cutoff, batch_size=batch_size,
    )

    result = {
        "error_logs_error": error_logs_error,
        "error_logs_warning": error_logs_warning,
        "task_observations": task_observations,
        "milestones": milestones,
        "llm_attempt_events": llm_attempts,
    }
    return result
