"""任务观测服务。

提供 `record_task_observation` 幂等式写入函数; 对三业务流程的 task 入口/出口透明埋点。
错误静默 + 不阻塞主流程是核心约定。

使用约定:
    from app.services.task_observation_service import (
        record_task_observation, TaskFlow, TaskPhase, TaskOutcome,
    )

    async def run_something(project_id: int):
        await record_task_observation(
            task_id=f"tender:{project_id}:review",
            flow=TaskFlow.TENDER,
            task_type="review",
            phase=TaskPhase.START,
            project_id=project_id,
        )
        try:
            ...
            await record_task_observation(
                task_id=f"tender:{project_id}:review",
                flow=TaskFlow.TENDER,
                task_type="review",
                phase=TaskPhase.END,
                project_id=project_id,
                outcome=TaskOutcome.SUCCESS,
                duration_ms=...,
            )
        except Exception as e:
            await record_task_observation(..., phase=TaskPhase.END, outcome=TaskOutcome.FAILURE, last_error=str(e))
            raise
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.task_run import TaskRun, TaskRunStatus, span_already_covered
from app.models.task_observation import (
    TaskFlow,
    TaskObservation,
    TaskOutcome,
    TaskPhase,
)


async def record_task_observation(
    *,
    task_id: str,
    flow: TaskFlow | str,
    task_type: str,
    phase: TaskPhase | str,
    project_id: Optional[int] = None,
    stage: Optional[str] = None,
    outcome: Optional[TaskOutcome | str] = None,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
    duration_ms: Optional[int] = None,
    retry_count: int = 0,
    failure_state: Optional[str] = None,
    last_error: Optional[str] = None,
    degraded: bool = False,
    labels: Optional[dict[str, Any]] = None,
    db: Optional[AsyncSession] = None,
) -> None:
    """幂等式记录一次任务观测事件。

    - 内部 try/except 包裹, 失败仅记录 warning, 不影响主流程
    - 若传入 db, 使用该 session (事务由调用方负责); 否则自建独立 session 并 commit
    """
    try:
        flow_val = flow.value if isinstance(flow, TaskFlow) else str(flow)
        phase_val = phase.value if isinstance(phase, TaskPhase) else str(phase)
        outcome_val: Optional[str] = None
        if outcome is not None:
            outcome_val = outcome.value if isinstance(outcome, TaskOutcome) else str(outcome)

        # 截短错误摘要
        error_summary: Optional[str] = None
        if last_error:
            raw = str(last_error).strip().splitlines()[0]
            error_summary = raw[:500]

        labels_json: Optional[str] = None
        if labels:
            try:
                labels_json = json.dumps(labels, ensure_ascii=False, default=str)[:4000]
            except Exception:
                labels_json = None

        obs = TaskObservation(
            task_id=task_id[:128],
            flow=flow_val,
            task_type=task_type[:64],
            phase=phase_val,
            stage=stage[:64] if stage else None,
            project_id=project_id,
            outcome=outcome_val,
            started_at=started_at,
            ended_at=ended_at or (datetime.now(timezone.utc) if phase_val == TaskPhase.END.value else None),
            duration_ms=duration_ms,
            retry_count=retry_count,
            failure_state=failure_state,
            last_error=error_summary,
            degraded=1 if degraded else 0,
            labels_json=labels_json,
        )

        if db is not None:
            db.add(obs)
            return

        async with AsyncSessionLocal() as session:
            session.add(obs)
            await session.commit()
    except Exception as e:  # 绝不抛出
        logger.warning(f"record_task_observation 失败 (忽略): {e}")


async def persist_observed_duration(
    *,
    task_id: str,
    flow: TaskFlow | str,
    task_type: str,
    project_id: Optional[int],
    started_at: Optional[datetime],
    ended_at: Optional[datetime],
    duration_ms: Optional[int],
) -> None:
    """成功观测结束时补一条冻结耗时。已有完成运行覆盖该时段则不重复记。"""
    try:
        if project_id is None or not duration_ms or duration_ms <= 0 or started_at is None or ended_at is None:
            return
        flow_val = flow.value if isinstance(flow, TaskFlow) else str(flow)
        node_key = f"observed:{task_type}"[:128]
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(TaskRun.created_at, TaskRun.updated_at)
                .where(TaskRun.project_id == project_id)
                .where(TaskRun.status == TaskRunStatus.completed.value)
                .where(TaskRun.duration_ms.is_not(None))
                .where(TaskRun.duration_ms > 0)
            )).all()
            if span_already_covered(rows, started_at, ended_at):
                return
            attempt = (await session.execute(
                select(func.coalesce(func.max(TaskRun.attempt), 0))
                .where(TaskRun.project_id == project_id)
                .where(TaskRun.flow == flow_val)
                .where(TaskRun.node_key == node_key)
            )).scalar_one()
            session.add(TaskRun(
                project_id=project_id,
                flow=flow_val[:64],
                node_key=node_key,
                task_name=(task_id or node_key)[:128],
                status=TaskRunStatus.completed.value,
                attempt=int(attempt) + 1,
                duration_ms=int(duration_ms),
                created_at=started_at,
                updated_at=ended_at,
            ))
            await session.commit()
    except Exception as e:  # 绝不抛出
        logger.warning(f"persist_observed_duration 失败 (忽略): {e}")


# observe_task / observed_task / safe_duration_ms 等装饰器实现位于
# app.core.observability.task_observation (避免触发 app.services 循环导入).
# 这里 re-export 以兼容历史调用点 (若直接 import 本模块的装饰器).
from app.core.observability.task_observation import (  # noqa: E402,F401
    observe_task,
    observed_task,
    safe_duration_ms,
)


