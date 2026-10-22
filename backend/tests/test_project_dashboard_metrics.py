"""控制台派生指标：行动桶、卡滞与确认闸、成功率空样本。"""

from datetime import datetime, timedelta, timezone

from app.models.task_run import TaskRun, freeze_completed_duration, span_already_covered
from app.services.project_dashboard_metrics import (
    STALL_HOURS,
    DashboardProjectSnapshot,
    ProcessingSpan,
    active_processing_ms,
    build_dashboard_derived,
)

_UNSET = object()


def _row(
    status: str,
    created_at: datetime,
    updated_at: datetime,
    project_type: str = "bid_review",
    task_duration_ms: int | None = None,
    result_at: datetime | None | object = _UNSET,
    has_uploaded_file: bool = True,
) -> DashboardProjectSnapshot:
    if result_at is _UNSET:
        result_at = updated_at if task_duration_ms else None
    return DashboardProjectSnapshot(
        status=status,
        project_type=project_type,
        created_at=created_at,
        updated_at=updated_at,
        task_duration_ms=task_duration_ms,
        result_at=result_at,  # type: ignore[arg-type]
        has_uploaded_file=has_uploaded_file,
    )


def test_confirm_gates_and_stall_are_separate_mechanisms() -> None:
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row("outlined", now - timedelta(hours=1), now - timedelta(minutes=20)),
        _row("matched", now - timedelta(hours=1), now - timedelta(minutes=10)),
        _row("extracted", now - timedelta(days=3), now - timedelta(hours=5)),
        _row("parsing", now - timedelta(days=2), now - timedelta(hours=STALL_HOURS + 1)),
        _row("reviewing", now - timedelta(hours=1), now - timedelta(minutes=5)),
        _row("completed", now - timedelta(days=10), now - timedelta(days=9)),
    ]

    derived = build_dashboard_derived(rows, now, 7)
    by_id = {item.id: item for item in derived.action_queue}

    assert [item.id for item in derived.action_queue] == [
        "failed",
        "stalled",
        "awaiting_confirm",
        "awaiting_upload",
    ]
    assert by_id["awaiting_confirm"].count == 3
    assert set(by_id["awaiting_confirm"].filter.stages) == {"extracted", "outlined", "matched"}
    assert by_id["stalled"].count == 2
    assert by_id["stalled"].filter.stale_hours == STALL_HOURS
    assert by_id["stalled"].filter.status == "processing"
    assert by_id["failed"].count == 0

    groups = {group.key: group for group in derived.stage_groups}
    assert groups["awaiting_confirm"].count == 3
    assert groups["document_handling"].count == 1
    assert groups["ai_analysis"].count == 1
    assert "completed" not in groups


def test_success_rate_skips_terminal_projects_that_never_uploaded() -> None:
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row("completed", now, now, "bid_review"),
        _row("failed", now, now, "bid_review"),
        _row("completed", now, now, "bid_writing", has_uploaded_file=False),
        _row("failed", now, now, "collusion_check", has_uploaded_file=False),
        _row("created", now, now, "bid_writing", has_uploaded_file=False),
    ]
    derived = build_dashboard_derived(rows, now, 7)
    assert derived.health.success_rate == 1 / 2


def test_success_rate_is_null_without_terminal_samples_and_skips_negative_duration() -> None:
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    empty = build_dashboard_derived([], now, 7)
    assert empty.health.success_rate is None
    assert [item.avg_duration_ms for item in empty.health.completion_by_type] == [None, None, None]
    assert empty.health.backlog == 0
    assert len(empty.daily_completed) == 7

    rows = [
        _row("completed", now - timedelta(days=14), now, "bid_writing", 120_000),
        _row("completed", now - timedelta(days=20), now, "bid_writing", 360_000),
        _row("completed", now - timedelta(seconds=1200), now, "bid_writing"),
        _row("completed", now - timedelta(days=10), now, "bid_review", 456_000),
        _row("completed", now - timedelta(seconds=30), now, "collusion_check", 341_183),
        _row("completed", now - timedelta(seconds=40), now, "collusion_check", 114_492),
        _row("failed", now - timedelta(days=1), now - timedelta(hours=1), "collusion_check", 9_000_000),
        _row("created", now - timedelta(hours=2), now - timedelta(hours=2), "bid_writing", 50_000),
    ]
    derived = build_dashboard_derived(rows, now, 7)
    by_type = {item.project_type: item for item in derived.health.completion_by_type}
    assert derived.health.success_rate == 6 / 7
    assert by_type["bid_writing"].avg_duration_ms == 240_000
    assert by_type["bid_writing"].sample_count == 2
    assert by_type["bid_review"].avg_duration_ms == 456_000
    assert by_type["collusion_check"].avg_duration_ms == (341_183 + 114_492) / 2
    assert by_type["collusion_check"].sample_count == 2
    assert derived.health.backlog == 1
    assert derived.daily_completed[-1].count == 6


def test_health_window_moves_flow_metrics_without_the_trend_window_or_backlog() -> None:
    now = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row("completed", now - timedelta(days=1), now - timedelta(days=1), "bid_writing", 60_000),
        _row("completed", now - timedelta(days=20), now - timedelta(days=20), "bid_writing", 180_000),
        _row("completed", now - timedelta(days=12), now - timedelta(days=12), "collusion_check", 90_000),
        _row("failed", now - timedelta(days=2), now - timedelta(days=2), "bid_review"),
        _row("failed", now - timedelta(days=18), now - timedelta(days=18), "bid_review"),
        _row("created", now - timedelta(days=40), now - timedelta(days=1), "collusion_check"),
        _row("reviewing", now - timedelta(hours=3), now - timedelta(minutes=10), "bid_review"),
    ]

    short = build_dashboard_derived(rows, now, 90, 7)
    long = build_dashboard_derived(rows, now, 7, 30)
    short_by_type = {item.project_type: item for item in short.health.completion_by_type}
    long_by_type = {item.project_type: item for item in long.health.completion_by_type}

    assert len(short.daily_completed) == 90
    assert len(long.daily_completed) == 7
    assert short.health.backlog == 2
    assert long.health.backlog == 2
    assert short.health.success_rate == 1 / 2
    assert long.health.success_rate == 3 / 5
    assert short.health.net_change == 2
    assert long.health.net_change == 3
    assert short_by_type["bid_writing"].avg_duration_ms == 60_000
    assert short_by_type["bid_writing"].sample_count == 1
    assert short_by_type["collusion_check"].avg_duration_ms is None
    assert long_by_type["bid_writing"].avg_duration_ms == 120_000
    assert long_by_type["bid_writing"].sample_count == 2
    assert long_by_type["collusion_check"].avg_duration_ms == 90_000
    assert long_by_type["collusion_check"].sample_count == 1


def test_duration_follows_result_time_and_skips_projects_without_output() -> None:
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row(
            "completed", now - timedelta(days=40), now, "bid_review", 200_000,
            result_at=now - timedelta(days=40),
        ),
        _row(
            "completed", now - timedelta(days=1), now, "bid_writing", 50_000,
            result_at=None,
        ),
        _row(
            "completed", now - timedelta(days=2), now - timedelta(days=2), "collusion_check", 90_000,
            result_at=now - timedelta(days=2),
        ),
    ]

    short = build_dashboard_derived(rows, now, 90, 7)
    long = build_dashboard_derived(rows, now, 7, 90)
    short_by_type = {item.project_type: item for item in short.health.completion_by_type}
    long_by_type = {item.project_type: item for item in long.health.completion_by_type}

    assert short.health.success_rate == 1
    assert short_by_type["bid_review"].avg_duration_ms is None
    assert short_by_type["bid_writing"].avg_duration_ms is None
    assert short_by_type["collusion_check"].avg_duration_ms == 90_000
    assert long_by_type["bid_review"].avg_duration_ms == 200_000
    assert long_by_type["bid_review"].sample_count == 1
    assert long_by_type["bid_writing"].sample_count == 0
    assert long_by_type["collusion_check"].avg_duration_ms == 90_000


def test_active_processing_keeps_work_until_the_result_and_drops_later_success() -> None:
    created = datetime(2026, 9, 1, tzinfo=timezone.utc)
    review_result = datetime(2026, 9, 21, 8, tzinfo=timezone.utc)
    review_ms = active_processing_ms(
        [
            ProcessingSpan(created, created + timedelta(minutes=8), 480_000),
            ProcessingSpan(review_result + timedelta(days=8), review_result + timedelta(days=8, minutes=4), 240_000),
        ],
        created_at=created,
        result_at=review_result,
    )
    render_at = datetime(2026, 9, 18, 3, tzinfo=timezone.utc)
    writing_ms = active_processing_ms(
        [
            ProcessingSpan(created + timedelta(hours=1), created + timedelta(hours=1, minutes=12), 720_000),
            ProcessingSpan(render_at + timedelta(hours=2), render_at + timedelta(hours=2, minutes=3), 180_000),
        ],
        created_at=created,
        result_at=render_at,
    )

    assert review_ms == 480_000
    assert writing_ms == 720_000
    assert active_processing_ms(
        [ProcessingSpan(created, created + timedelta(minutes=1), 60_000)],
        created_at=created,
        result_at=None,
    ) is None


def test_freeze_completed_duration_keeps_the_first_value() -> None:
    run = TaskRun(
        project_id=1,
        flow="writer",
        node_key="compose",
        task_name="compose",
        status="completed",
        created_at=datetime(2026, 7, 13, 6, 22, tzinfo=timezone.utc),
    )
    freeze_completed_duration(run, now=datetime(2026, 7, 13, 6, 24, tzinfo=timezone.utc))
    assert run.duration_ms == 120_000
    freeze_completed_duration(run, now=datetime(2026, 10, 6, tzinfo=timezone.utc))
    assert run.duration_ms == 120_000


def test_observed_span_is_kept_when_no_run_covers_it_and_skipped_when_one_does() -> None:
    started = datetime(2026, 9, 20, 1, tzinfo=timezone.utc)
    ended = datetime(2026, 9, 20, 2, tzinfo=timezone.utc)
    assert span_already_covered([], started, ended) is False
    review_run = (
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 1, tzinfo=timezone.utc),
    )
    assert span_already_covered([review_run], started, ended) is False
    writing_run = (
        datetime(2026, 9, 20, 1, 5, tzinfo=timezone.utc),
        datetime(2026, 9, 20, 1, 40, tzinfo=timezone.utc),
    )
    assert span_already_covered([writing_run], started, ended) is True
