"""
职责: 从已加载的项目行派生控制台健康、行动队列、办理阶段与完成趋势。
不做: 不访问数据库、不读取运维日志或任务心跳、不修改项目状态。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models.project import ProjectStatus, ProjectType
from app.schemas.project_dashboard import (
    ProjectDailyCount,
    ProjectDashboardActionFilter,
    ProjectDashboardActionItem,
    ProjectDashboardHealth,
    ProjectDashboardStageGroup,
    ProjectTypeCompletionDuration,
)

# 与状态组「处理中」同源：卡滞只看仍在推进、且超过阈值未更新的项目。
PROCESSING_STATUSES: frozenset[str] = frozenset({
    ProjectStatus.UPLOADING.value,
    ProjectStatus.PARSING.value,
    ProjectStatus.PARSED.value,
    ProjectStatus.EXTRACTING.value,
    ProjectStatus.EXTRACTED.value,
    ProjectStatus.REVIEWING.value,
    ProjectStatus.OUTLINING.value,
    ProjectStatus.OUTLINED.value,
    ProjectStatus.MATCHING.value,
    ProjectStatus.MATCHED.value,
    ProjectStatus.WRITING.value,
    ProjectStatus.COMPOSED.value,
    ProjectStatus.FORMATTING.value,
})

STALL_HOURS = 2

# 一级办理阶段。人工确认闸不按业务域拆路径，成员状态即契约。
STAGE_GROUP_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("awaiting_material", "等待材料", (ProjectStatus.CREATED.value, ProjectStatus.UPLOADING.value)),
    ("document_handling", "文档处理中", (
        ProjectStatus.PARSING.value,
        ProjectStatus.PARSED.value,
        ProjectStatus.EXTRACTING.value,
    )),
    ("awaiting_confirm", "等待人工确认", (
        ProjectStatus.EXTRACTED.value,
        ProjectStatus.OUTLINED.value,
        ProjectStatus.MATCHED.value,
    )),
    ("ai_analysis", "AI分析中", (
        ProjectStatus.REVIEWING.value,
        ProjectStatus.OUTLINING.value,
        ProjectStatus.MATCHING.value,
        ProjectStatus.WRITING.value,
    )),
    ("result_generating", "结果生成中", (
        ProjectStatus.COMPOSED.value,
        ProjectStatus.FORMATTING.value,
    )),
)

AWAITING_CONFIRM_STATUSES: tuple[str, ...] = next(
    members for key, _label, members in STAGE_GROUP_SPECS if key == "awaiting_confirm"
)
AWAITING_UPLOAD_STATUSES: tuple[str, ...] = (ProjectStatus.CREATED.value,)

COMPLETION_DURATION_TYPES: tuple[tuple[str, str], ...] = (
    (ProjectType.BID_WRITING.value, "标书编写"),
    (ProjectType.BID_REVIEW.value, "投标审查"),
    (ProjectType.COLLUSION_CHECK.value, "串标审查"),
)


@dataclass(frozen=True)
class ProcessingSpan:
    """一次已经成功结束的任务片段。"""

    started_at: datetime | None
    ended_at: datetime | None
    duration_ms: int


@dataclass(frozen=True)
class DashboardProjectSnapshot:
    """指标计算所需的项目快照。"""

    status: str
    project_type: str
    created_at: datetime
    updated_at: datetime
    task_duration_ms: int | None = None
    result_at: datetime | None = None
    has_uploaded_file: bool = True


@dataclass(frozen=True)
class DashboardDerived:
    """控制台新增派生块。"""

    health: ProjectDashboardHealth
    action_queue: list[ProjectDashboardActionItem]
    daily_completed: list[ProjectDailyCount]
    stage_groups: list[ProjectDashboardStageGroup]


def status_group(status: str) -> str:
    """把细粒度状态收成待处理/处理中/已完成/失败。"""
    if status == ProjectStatus.CREATED.value:
        return "pending"
    if status in PROCESSING_STATUSES:
        return "processing"
    if status == ProjectStatus.COMPLETED.value:
        return "completed"
    if status == ProjectStatus.FAILED.value:
        return "failed"
    return "processing"


def build_dashboard_derived(
    rows: Iterable[DashboardProjectSnapshot],
    generated_at: datetime,
    window_days: int,
    health_window_days: int | None = None,
) -> DashboardDerived:
    """派生健康指标、行动队列、完成趋势和一级办理阶段。

    window_days 只决定新建/完成趋势；health_window_days 只决定健康流量指标。
    缺省时健康窗口与趋势窗口相同，便于只关心一个窗口的调用方。
    """
    snapshots = [
        DashboardProjectSnapshot(
            status=row.status,
            project_type=row.project_type,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
            task_duration_ms=row.task_duration_ms,
            result_at=_as_utc(row.result_at) if row.result_at is not None else None,
            has_uploaded_file=row.has_uploaded_file,
        )
        for row in rows
    ]
    moment = _as_utc(generated_at)
    health_days = window_days if health_window_days is None else health_window_days
    health_start, health_end = _window_dates(moment, health_days)
    trend_start, _trend_end = _window_dates(moment, window_days)
    return DashboardDerived(
        health=_build_health(snapshots, health_start, health_end),
        action_queue=_build_action_queue(snapshots, moment),
        daily_completed=_build_daily_completed(snapshots, trend_start, window_days),
        stage_groups=_build_stage_groups(snapshots),
    )


def _build_health(
    rows: Sequence[DashboardProjectSnapshot],
    start_date,
    end_date,
) -> ProjectDashboardHealth:
    completed = 0
    failed = 0
    durations_by_type: dict[str, list[float]] = {key: [] for key, _label in COMPLETION_DURATION_TYPES}
    for row in rows:
        in_touch_window = _on_window_date(row.updated_at, start_date, end_date)
        counts_as_closure = in_touch_window and row.has_uploaded_file
        if counts_as_closure and row.status == ProjectStatus.COMPLETED.value:
            completed += 1
        elif counts_as_closure and row.status == ProjectStatus.FAILED.value:
            failed += 1
        if (
            row.status == ProjectStatus.COMPLETED.value
            and row.result_at is not None
            and _on_window_date(row.result_at, start_date, end_date)
            and row.task_duration_ms is not None
            and row.task_duration_ms > 0
            and row.project_type in durations_by_type
        ):
            durations_by_type[row.project_type].append(float(row.task_duration_ms))

    terminal = completed + failed
    backlog = sum(1 for row in rows if status_group(row.status) in {"pending", "processing"})
    return ProjectDashboardHealth(
        success_rate=(completed / terminal) if terminal else None,
        completion_by_type=[
            ProjectTypeCompletionDuration(
                project_type=key,
                label=label,
                avg_duration_ms=_average(durations_by_type[key]),
                sample_count=len(durations_by_type[key]),
            )
            for key, label in COMPLETION_DURATION_TYPES
        ],
        backlog=backlog,
        net_change=_net_change(rows, start_date, end_date),
    )


def _build_action_queue(
    rows: Sequence[DashboardProjectSnapshot],
    generated_at: datetime,
) -> list[ProjectDashboardActionItem]:
    stall_before = generated_at - timedelta(hours=STALL_HOURS)
    stalled = sum(
        1
        for row in rows
        if row.status in PROCESSING_STATUSES and row.updated_at < stall_before
    )
    confirm = sum(1 for row in rows if row.status in AWAITING_CONFIRM_STATUSES)
    upload = sum(1 for row in rows if row.status in AWAITING_UPLOAD_STATUSES)
    failed = sum(1 for row in rows if row.status == ProjectStatus.FAILED.value)
    return [
        ProjectDashboardActionItem(
            id="failed",
            label="失败待处置",
            count=failed,
            tone="red",
            action_label="查看失败",
            filter=ProjectDashboardActionFilter(status="failed"),
        ),
        ProjectDashboardActionItem(
            id="stalled",
            label="长时间未推进",
            count=stalled,
            tone="orange",
            action_label="查看卡滞",
            filter=ProjectDashboardActionFilter(status="processing", stale_hours=STALL_HOURS),
        ),
        ProjectDashboardActionItem(
            id="awaiting_confirm",
            label="待人工确认",
            count=confirm,
            tone="amber",
            action_label="去确认",
            filter=ProjectDashboardActionFilter(stages=list(AWAITING_CONFIRM_STATUSES)),
        ),
        ProjectDashboardActionItem(
            id="awaiting_upload",
            label="待上传材料",
            count=upload,
            tone="cyan",
            action_label="去上传",
            filter=ProjectDashboardActionFilter(stages=list(AWAITING_UPLOAD_STATUSES)),
        ),
    ]


def _build_daily_completed(
    rows: Sequence[DashboardProjectSnapshot],
    start_date,
    window_days: int,
) -> list[ProjectDailyCount]:
    counts = {
        (start_date + timedelta(days=offset)).isoformat(): 0
        for offset in range(window_days)
    }
    for row in rows:
        if row.status != ProjectStatus.COMPLETED.value:
            continue
        key = row.updated_at.date().isoformat()
        if key in counts:
            counts[key] += 1
    return [ProjectDailyCount(date=date_key, count=counts[date_key]) for date_key in counts]


def _build_stage_groups(rows: Sequence[DashboardProjectSnapshot]) -> list[ProjectDashboardStageGroup]:
    counts = {key: 0 for key, _label, _members in STAGE_GROUP_SPECS}
    members = {key: members for key, _label, members in STAGE_GROUP_SPECS}
    status_to_group = {
        status: key
        for key, _label, statuses in STAGE_GROUP_SPECS
        for status in statuses
    }
    for row in rows:
        group_key = status_to_group.get(row.status)
        if group_key is not None:
            counts[group_key] += 1
    return [
        ProjectDashboardStageGroup(
            key=key,
            label=label,
            count=counts[key],
            member_statuses=list(members[key]),
        )
        for key, label, _statuses in STAGE_GROUP_SPECS
    ]


def active_processing_ms(
    spans: Sequence[ProcessingSpan],
    *,
    created_at: datetime,
    result_at: datetime | None,
) -> int | None:
    """
    职责: 汇总创建到最终结果产出之间、已经成功结束的任务耗时。
    不做: 不计入失败/取消/中断，不按业务类型分支，不访问数据库。
    """
    if result_at is None:
        return None
    created = _as_utc(created_at)
    result = _as_utc(result_at)
    total = 0
    for span in spans:
        if span.duration_ms <= 0:
            continue
        started = _as_utc(span.started_at) if span.started_at is not None else None
        ended = _as_utc(span.ended_at) if span.ended_at is not None else None
        if started is not None and started > result:
            continue
        if ended is not None and ended <= created:
            continue
        # ponytail: 缺起止时间的旧片段整段计入；观测都带 ended_at 后可删
        total += span.duration_ms
    return total or None


def _net_change(rows: Sequence[DashboardProjectSnapshot], start_date, end_date) -> int:
    """窗口内新建数减办结数。办结日按 updated_at 近似，与成功率同一窗口。"""
    created = sum(1 for row in rows if _on_window_date(row.created_at, start_date, end_date))
    completed = sum(
        1
        for row in rows
        if row.status == ProjectStatus.COMPLETED.value and _on_window_date(row.updated_at, start_date, end_date)
    )
    return created - completed


def _window_dates(generated_at: datetime, window_days: int):
    end_date = generated_at.date()
    start_date = end_date - timedelta(days=window_days - 1)
    return start_date, end_date


def _on_window_date(moment: datetime, start_date, end_date) -> bool:
    day = moment.date()
    return start_date <= day <= end_date


def _average(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
