"""显式重试任务运行记录。"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from collections.abc import Sequence

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from app.models.base import BaseModel


class TaskRunStatus(str, enum.Enum):
    """显式重试任务运行状态。"""

    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TaskRun(BaseModel):
    """跨业务节点的显式重试主记录。

    TaskRun 保存当前 attempt、操作者、失败节点和 reset/preserved 审计摘要。
    TaskObservation 仍只记录事件流，两者职责互不替代。
    """

    __tablename__ = "task_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "flow",
            "node_key",
            "attempt",
            name="uq_task_runs_project_flow_node_attempt",
        ),
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID",
    )
    flow = Column(String(64), nullable=False, index=True, comment="业务流")
    node_key = Column(String(128), nullable=False, index=True, comment="节点标识")
    task_name = Column(String(128), nullable=False, index=True, comment="后台任务名")
    status = Column(
        String(32),
        nullable=False,
        default=TaskRunStatus.queued.value,
        index=True,
        comment="运行状态",
    )
    attempt = Column(Integer, nullable=False, default=1, comment="节点运行轮次")
    current_arq_job_id = Column(String(128), nullable=True, comment="当前 ARQ job id")
    last_stage = Column(String(64), nullable=True, comment="最近阶段")
    last_error = Column(Text, nullable=True, comment="最近错误摘要")
    retry_requested_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="触发操作者ID",
    )
    retry_requested_at = Column(DateTime(timezone=True), nullable=True, comment="触发时间")
    preserved_summary_json = Column(Text, nullable=True, comment="保留产物摘要 JSON")
    reset_scope_json = Column(Text, nullable=True, comment="重置范围摘要 JSON")
    duration_ms = Column(
        Integer,
        nullable=True,
        comment="首次进入完成态时冻结的耗时毫秒，之后不改写",
    )

    def __repr__(self) -> str:
        return (
            f"<TaskRun(project_id={self.project_id}, flow='{self.flow}', "
            f"node='{self.node_key}', attempt={self.attempt}, status='{self.status}')>"
        )


def freeze_completed_duration(run: TaskRun, *, now: datetime | None = None) -> None:
    """
    职责: 运行首次进入完成态时，按创建到当前时刻冻结耗时。
    不做: 不改状态、不覆盖已有耗时、不访问数据库。
    """
    if run.duration_ms is not None:
        return
    started = run.created_at
    if started is None:
        return
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    elapsed = int((end - started).total_seconds() * 1000)
    if elapsed > 0:
        run.duration_ms = elapsed


def span_already_covered(
    runs: Sequence[tuple[datetime | None, datetime | None]],
    started_at: datetime,
    ended_at: datetime,
) -> bool:
    """
    职责: 判断已有完成运行记录是否已覆盖这段成功观测。
    不做: 不写库、不按业务类型分支。
    """
    started = _aware(started_at)
    ended = _aware(ended_at)
    for created_at, updated_at in runs:
        if created_at is None or updated_at is None:
            continue
        if _aware(created_at) <= ended and _aware(updated_at) >= started:
            return True
    return False


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
