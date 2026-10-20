"""项目删除时的写标生成任务清理。

职责: 从活跃 TaskRun 收集精确 task_name/current_arq_job_id 并取消本地任务。
不做: 不删除项目或业务数据，不写取消墓碑，不处理其它流程任务。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tasks import cancel_task, get_task
from app.models.task_run import TaskRun, TaskRunStatus
from app.services.retry_service import RetryService


@dataclass(frozen=True)
class WritingComposeTaskTargets:
    task_names: list[str]
    arq_job_ids: list[str]


@dataclass
class WritingComposeCancelResult:
    cancelled_names: list[str] = field(default_factory=list)
    tasks_to_await: list[asyncio.Task] = field(default_factory=list)


class ProjectWritingTaskCleanup:
    """
    职责: 收集并取消项目内写标全文/单章后台任务。
    不做: 不删除项目、文档或业务数据。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def collect_targets(self, project_id: int) -> WritingComposeTaskTargets:
        result = await self.db.execute(
            select(TaskRun).where(
                TaskRun.project_id == project_id,
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
        names: list[str] = []
        job_ids: list[str] = []
        seen_jobs: set[str] = set()
        for run in result.scalars().all():
            task_name = str(getattr(run, "task_name", "") or "")
            if task_name:
                names.append(task_name)
            job_id = getattr(run, "current_arq_job_id", None)
            if job_id and job_id != "arq-job-unknown" and job_id not in seen_jobs:
                seen_jobs.add(job_id)
                job_ids.append(job_id)
        return WritingComposeTaskTargets(task_names=names, arq_job_ids=job_ids)

    def cancel_local_tasks(
        self, targets: WritingComposeTaskTargets
    ) -> WritingComposeCancelResult:
        cancelled_names: list[str] = []
        tasks_to_await: list[asyncio.Task] = []
        for task_name in targets.task_names:
            task_obj = get_task(task_name)
            if cancel_task(task_name):
                cancelled_names.append(task_name)
                if task_obj is not None and not task_obj.done():
                    tasks_to_await.append(task_obj)
        return WritingComposeCancelResult(
            cancelled_names=cancelled_names,
            tasks_to_await=tasks_to_await,
        )
