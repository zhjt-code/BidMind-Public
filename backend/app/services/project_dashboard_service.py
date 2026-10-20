"""项目控制台聚合服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collusion_result import CollusionResult
from app.models.document import Document
from app.models.project import Project, ProjectStatus, ProjectType
from app.models.review_result import ReviewResult
from app.models.task_run import TaskRun, TaskRunStatus
from app.models.user import User, UserRole
from app.models.writing_material import WritingMaterial
from app.models.writing_render_artifact import WritingRenderArtifact
from app.schemas.project_dashboard import (
    ProjectDailyCount,
    ProjectDashboardBucket,
    ProjectDashboardRecentProject,
    ProjectDashboardResponse,
    ProjectDashboardSummary,
    ProjectTypeStatusRow,
)
from app.services.project_dashboard_metrics import (
    DashboardProjectSnapshot,
    ProcessingSpan,
    active_processing_ms,
    build_dashboard_derived,
    status_group,
)


PROJECT_TYPE_LABELS: tuple[tuple[str, str], ...] = (
    (ProjectType.BID_WRITING.value, "标书编写"),
    (ProjectType.BID_REVIEW.value, "投标审查"),
    (ProjectType.COLLUSION_CHECK.value, "串标审查"),
)

STATUS_GROUP_LABELS: tuple[tuple[str, str], ...] = (
    ("pending", "待处理"),
    ("processing", "处理中"),
    ("completed", "已完成"),
    ("failed", "失败"),
)

STATUS_LABELS: tuple[tuple[str, str], ...] = (
    (ProjectStatus.CREATED.value, "待上传"),
    (ProjectStatus.UPLOADING.value, "上传中"),
    (ProjectStatus.PARSING.value, "解析中"),
    (ProjectStatus.PARSED.value, "待继续"),
    (ProjectStatus.EXTRACTING.value, "提取中"),
    (ProjectStatus.EXTRACTED.value, "待确认"),
    (ProjectStatus.REVIEWING.value, "AI分析中"),
    (ProjectStatus.OUTLINING.value, "生成目录中"),
    (ProjectStatus.OUTLINED.value, "目录就绪"),
    (ProjectStatus.MATCHING.value, "素材匹配中"),
    (ProjectStatus.MATCHED.value, "匹配就绪"),
    (ProjectStatus.WRITING.value, "内容生成中"),
    (ProjectStatus.COMPOSED.value, "预览就绪"),
    (ProjectStatus.FORMATTING.value, "格式渲染中"),
    (ProjectStatus.COMPLETED.value, "已完成"),
    (ProjectStatus.FAILED.value, "失败"),
)


@dataclass(frozen=True)
class _ProjectDashboardRow:
    id: int
    name: str
    project_type: str
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectDashboardService:
    """
    职责: 聚合调用者可见项目的控制台统计与趋势。
    不做: 不读取错误日志、任务心跳或观测日志，不修改项目状态。耗时只累计最终结果产出前、已冻结在运行记录上的完成步骤。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_dashboard(
        self,
        user: User,
        window_days: int = 30,
        health_window_days: int = 30,
    ) -> ProjectDashboardResponse:
        """返回用户可见项目的只读控制台聚合数据。"""
        safe_window_days = _clamp_window_days(window_days)
        safe_health_window_days = _clamp_window_days(health_window_days)
        generated_at = datetime.now(timezone.utc)
        rows = await self._load_visible_projects(user)
        active_durations = await self._load_active_durations(rows)
        uploaded_ids = await self._load_uploaded_project_ids([row.id for row in rows])
        derived = build_dashboard_derived(
            (
                DashboardProjectSnapshot(
                    status=row.status,
                    project_type=row.project_type,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    task_duration_ms=active_durations.get(row.id, (None, None))[0],
                    result_at=active_durations.get(row.id, (None, None))[1],
                    has_uploaded_file=row.id in uploaded_ids,
                )
                for row in rows
            ),
            generated_at,
            safe_window_days,
            safe_health_window_days,
        )

        return ProjectDashboardResponse(
            scope="all" if user.role == UserRole.ADMIN else "mine",
            generated_at=generated_at,
            window_days=safe_window_days,
            health_window_days=safe_health_window_days,
            summary=self._build_summary(rows, generated_at),
            type_distribution=self._build_type_distribution(rows),
            status_distribution=self._build_status_distribution(rows),
            stage_distribution=self._build_stage_distribution(rows),
            type_status_matrix=self._build_type_status_matrix(rows),
            daily_created=self._build_daily_created(rows, generated_at, safe_window_days),
            daily_completed=derived.daily_completed,
            recent_projects=self._build_recent_projects(rows),
            health=derived.health,
            action_queue=derived.action_queue,
            stage_groups=derived.stage_groups,
        )

    async def _load_active_durations(
        self,
        rows: list[_ProjectDashboardRow],
    ) -> dict[int, tuple[int | None, datetime | None]]:
        """最终结果产出前的成功任务耗时，以及该产出时间。没有最终结果则耗时为空。"""
        if not rows:
            return {}
        ids_by_type: dict[str, list[int]] = {}
        for row in rows:
            ids_by_type.setdefault(row.project_type, []).append(row.id)
        spans = await self._load_success_spans([row.id for row in rows])
        clocks = await self._load_result_clocks(ids_by_type)
        loaded: dict[int, tuple[int | None, datetime | None]] = {}
        for row in rows:
            result_at = clocks.get(row.id)
            loaded[row.id] = (
                active_processing_ms(
                    spans.get(row.id, ()),
                    created_at=row.created_at,
                    result_at=result_at,
                ),
                result_at,
            )
        return loaded

    async def _load_success_spans(self, project_ids: list[int]) -> dict[int, list[ProcessingSpan]]:
        """已完成运行记录上冻结的耗时。观测日志会被清理，不作为样本。"""
        if not project_ids:
            return {}
        result = await self.db.execute(
            select(
                TaskRun.project_id,
                TaskRun.duration_ms,
                TaskRun.created_at,
            )
            .where(TaskRun.status == TaskRunStatus.completed.value)
            .where(TaskRun.project_id.in_(project_ids))
            .where(TaskRun.duration_ms.is_not(None))
            .where(TaskRun.duration_ms > 0)
        )
        spans: dict[int, list[ProcessingSpan]] = {}
        for project_id, duration_ms, started_at in result.all():
            if project_id is None or not duration_ms or started_at is None:
                continue
            started = _as_aware_utc(started_at)
            spans.setdefault(int(project_id), []).append(ProcessingSpan(
                started_at=started,
                ended_at=started + timedelta(milliseconds=int(duration_ms)),
                duration_ms=int(duration_ms),
            ))
        return spans

    async def _load_result_clocks(self, ids_by_type: dict[str, list[int]]) -> dict[int, datetime]:
        """最终产物的产出时间。产物表不同，平均耗时只消费这里返回的时间。"""
        clocks: dict[int, datetime] = {}
        review_ids = ids_by_type.get(ProjectType.BID_REVIEW.value, [])
        if review_ids:
            result = await self.db.execute(
                select(ReviewResult.project_id, func.max(ReviewResult.updated_at))
                .where(ReviewResult.project_id.in_(review_ids))
                .group_by(ReviewResult.project_id)
            )
            for project_id, moment in result.all():
                if project_id is not None and moment is not None:
                    clocks[int(project_id)] = _as_aware_utc(moment)
        collusion_ids = ids_by_type.get(ProjectType.COLLUSION_CHECK.value, [])
        if collusion_ids:
            result = await self.db.execute(
                select(CollusionResult.project_id, func.max(CollusionResult.updated_at))
                .where(CollusionResult.project_id.in_(collusion_ids))
                .group_by(CollusionResult.project_id)
            )
            for project_id, moment in result.all():
                if project_id is not None and moment is not None:
                    clocks[int(project_id)] = _as_aware_utc(moment)
        writing_ids = ids_by_type.get(ProjectType.BID_WRITING.value, [])
        if writing_ids:
            result = await self.db.execute(
                select(WritingRenderArtifact.project_id, WritingRenderArtifact.rendered_at)
                .where(WritingRenderArtifact.project_id.in_(writing_ids))
                .where(WritingRenderArtifact.rendered_at.is_not(None))
            )
            for project_id, moment in result.all():
                if project_id is not None and moment is not None:
                    clocks[int(project_id)] = _as_aware_utc(moment)
        return clocks

    async def _load_uploaded_project_ids(self, project_ids: list[int]) -> set[int]:
        """至少有一份项目文件或编写素材的项目。从未上传的不参与办结成功率。"""
        if not project_ids:
            return set()
        uploaded: set[int] = set()
        for model in (Document, WritingMaterial):
            result = await self.db.execute(
                select(model.project_id)
                .where(model.project_id.in_(project_ids))
                .distinct()
            )
            uploaded.update(int(project_id) for project_id in result.scalars().all() if project_id is not None)
        return uploaded

    async def _load_visible_projects(self, user: User) -> list[_ProjectDashboardRow]:
        query = select(
            Project.id,
            Project.name,
            Project.project_type,
            Project.status,
            Project.created_at,
            Project.updated_at,
        )
        if user.role != UserRole.ADMIN:
            query = query.where(Project.owner_id == user.id)

        result = await self.db.execute(query)
        rows: list[_ProjectDashboardRow] = []
        for project_id, name, project_type, status, created_at, updated_at in result.all():
            rows.append(_ProjectDashboardRow(
                id=int(project_id),
                name=str(name),
                project_type=_enum_value(project_type),
                status=_enum_value(status),
                created_at=_as_aware_utc(created_at),
                updated_at=_as_aware_utc(updated_at),
            ))
        return rows

    def _build_summary(
        self,
        rows: Iterable[_ProjectDashboardRow],
        generated_at: datetime,
    ) -> ProjectDashboardSummary:
        row_list = list(rows)
        by_group = _count_by_status_group(row_list)
        return ProjectDashboardSummary(
            total=len(row_list),
            pending=by_group["pending"],
            processing=by_group["processing"],
            completed=by_group["completed"],
            failed=by_group["failed"],
            this_month=sum(1 for row in row_list if _is_same_utc_month(row.created_at, generated_at)),
        )

    def _build_type_distribution(
        self,
        rows: Iterable[_ProjectDashboardRow],
    ) -> list[ProjectDashboardBucket]:
        counts = {key: 0 for key, _label in PROJECT_TYPE_LABELS}
        for row in rows:
            if row.project_type in counts:
                counts[row.project_type] += 1
        return [
            ProjectDashboardBucket(key=key, label=label, count=counts[key])
            for key, label in PROJECT_TYPE_LABELS
        ]

    def _build_status_distribution(
        self,
        rows: Iterable[_ProjectDashboardRow],
    ) -> list[ProjectDashboardBucket]:
        counts = _count_by_status_group(rows)
        return [
            ProjectDashboardBucket(key=key, label=label, count=counts[key])
            for key, label in STATUS_GROUP_LABELS
        ]

    def _build_stage_distribution(
        self,
        rows: Iterable[_ProjectDashboardRow],
    ) -> list[ProjectDashboardBucket]:
        counts = {key: 0 for key, _label in STATUS_LABELS}
        for row in rows:
            if row.status in counts:
                counts[row.status] += 1
        return [
            ProjectDashboardBucket(key=key, label=label, count=counts[key])
            for key, label in STATUS_LABELS
        ]

    def _build_type_status_matrix(
        self,
        rows: Iterable[_ProjectDashboardRow],
    ) -> list[ProjectTypeStatusRow]:
        matrix = {
            key: {group_key: 0 for group_key, _label in STATUS_GROUP_LABELS}
            for key, _label in PROJECT_TYPE_LABELS
        }
        for row in rows:
            if row.project_type in matrix:
                matrix[row.project_type][status_group(row.status)] += 1
        return [
            ProjectTypeStatusRow(
                project_type=key,
                label=label,
                pending=matrix[key]["pending"],
                processing=matrix[key]["processing"],
                completed=matrix[key]["completed"],
                failed=matrix[key]["failed"],
            )
            for key, label in PROJECT_TYPE_LABELS
        ]

    def _build_daily_created(
        self,
        rows: Iterable[_ProjectDashboardRow],
        generated_at: datetime,
        window_days: int,
    ) -> list[ProjectDailyCount]:
        end_date = generated_at.date()
        start_date = end_date - timedelta(days=window_days - 1)
        counts = {
            (start_date + timedelta(days=offset)).isoformat(): 0
            for offset in range(window_days)
        }
        for row in rows:
            key = row.created_at.date().isoformat()
            if key in counts:
                counts[key] += 1
        return [ProjectDailyCount(date=date_key, count=counts[date_key]) for date_key in counts]

    def _build_recent_projects(
        self,
        rows: Iterable[_ProjectDashboardRow],
    ) -> list[ProjectDashboardRecentProject]:
        sorted_rows = sorted(rows, key=lambda row: row.updated_at, reverse=True)[:6]
        return [
            ProjectDashboardRecentProject(
                id=row.id,
                name=row.name,
                project_type=row.project_type,
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in sorted_rows
        ]


def _clamp_window_days(window_days: int) -> int:
    return max(7, min(90, int(window_days)))


def _enum_value(value: object) -> str:
    return str(value.value) if hasattr(value, "value") else str(value)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _count_by_status_group(rows: Iterable[_ProjectDashboardRow]) -> dict[str, int]:
    counts = {key: 0 for key, _label in STATUS_GROUP_LABELS}
    for row in rows:
        counts[status_group(row.status)] += 1
    return counts


def _is_same_utc_month(value: datetime, reference: datetime) -> bool:
    return value.year == reference.year and value.month == reference.month
