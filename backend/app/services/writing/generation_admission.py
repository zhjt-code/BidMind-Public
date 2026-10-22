"""写标内容生成准入。

职责: 以 Project 行锁原子化同项目全文/单章 TaskRun 检查与创建。
不做: 不计费、不投递后台任务、不执行 LLM、不推进任务终态执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.tasks import inspect_task_liveness, is_task_active
from app.models.project import Project
from app.models.task_run import TaskRun, TaskRunStatus
from app.services.retry_service import RetryService


@dataclass(frozen=True)
class WritingGenerationClaimRequest:
    project_id: int
    node_key: str
    task_name: str
    user_id: Optional[int]
    target_id: Optional[int]
    explicit_retry: bool


@dataclass(frozen=True)
class WritingGenerationClaimResult:
    claimed: bool
    task_run: object
    active_task_name: str


def _is_generation_live(run: TaskRun) -> bool:
    if is_task_active(run.task_name):
        return True
    arq_function = (
        "writing_compose_job"
        if run.node_key == RetryService.NODE_WRITING_COMPOSE
        else "writing_compose_single_job"
    )
    liveness = inspect_task_liveness(run.task_name, arq_function=arq_function)
    return bool(liveness.is_live)


def _finalize_stale_run(run: TaskRun) -> None:
    run.status = TaskRunStatus.cancelled.value
    run.last_stage = "stale"
    run.last_error = "stale_generation_replaced"


class WritingGenerationAdmissionService:
    """
    职责: 原子化同项目全文/单章生成准入。
    不做: 不计费、不投递、不执行生成。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def claim_generation(
        self, request: WritingGenerationClaimRequest
    ) -> WritingGenerationClaimResult:
        project_result = await self.db.execute(
            select(Project).where(Project.id == request.project_id).with_for_update()
        )
        if project_result.scalar_one_or_none() is None:
            raise NotFoundError("项目不存在")

        runs_result = await self.db.execute(
            select(TaskRun).where(
                TaskRun.project_id == request.project_id,
                TaskRun.flow == RetryService.FLOW_BID_WRITING,
                or_(
                    TaskRun.node_key == RetryService.NODE_WRITING_COMPOSE,
                    TaskRun.node_key.like(
                        f"{RetryService.NODE_WRITING_COMPOSE_SINGLE}:%"
                    ),
                ),
                TaskRun.status.in_(
                    (TaskRunStatus.queued.value, TaskRunStatus.running.value)
                ),
            )
        )
        live_run: Optional[TaskRun] = None
        for run in runs_result.scalars().all():
            if _is_generation_live(run):
                if live_run is None:
                    live_run = run
                continue
            _finalize_stale_run(run)

        if live_run is not None:
            return WritingGenerationClaimResult(
                claimed=False,
                task_run=live_run,
                active_task_name=str(live_run.task_name),
            )

        retry_svc = RetryService(self.db)
        created = await retry_svc.begin_attempt(
            project_id=request.project_id,
            node_key=request.node_key,
            user_id=request.user_id,
            task_name=request.task_name,
            explicit_retry=request.explicit_retry,
            target_id=request.target_id,
            last_stage="queued",
        )
        return WritingGenerationClaimResult(
            claimed=True,
            task_run=created,
            active_task_name=request.task_name,
        )
