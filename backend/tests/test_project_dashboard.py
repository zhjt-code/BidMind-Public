"""项目控制台聚合接口测试。

覆盖普通用户/管理员统计范围、空数据补零、趋势补零和只读契约。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.collusion_result import CollusionResult
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus, ProjectType
from app.models.requirement import Requirement, RequirementCategory
from app.models.review_result import ComplianceLevel, ReviewResult
from app.models.task_observation import TaskObservation
from app.models.task_run import TaskRun
from app.models.writing_render_artifact import WritingRenderArtifact

_TS = str(int(time.time()))[-6:]


def _sync_session():
    engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"))
    Session = sessionmaker(bind=engine)
    return engine, Session()


def _register_and_login(client: TestClient, tag: str) -> dict[str, str]:
    user = {
        "username": f"dash_{tag}_{_TS}",
        "email": f"dash_{tag}_{_TS}@example.com",
        "password": "TestPass@123",
        "confirm_password": "TestPass@123",
    }
    register = client.post("/api/auth/register", json=user)
    assert register.status_code == 200, register.text
    login = client.post(
        "/api/auth/login",
        json={"username": user["username"], "password": user["password"]},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _insert_projects(rows: Iterable[tuple[str, int, ProjectType, ProjectStatus, datetime, datetime]]) -> list[int]:
    engine, session = _sync_session()
    project_ids: list[int] = []
    try:
        for name, owner_id, project_type, status, created_at, updated_at in rows:
            project = Project(
                name=name,
                owner_id=owner_id,
                project_type=project_type,
                status=status,
                created_at=created_at,
                updated_at=updated_at,
            )
            session.add(project)
            session.flush()
            project_ids.append(int(project.id))
        session.commit()
        return project_ids
    finally:
        session.close()
        engine.dispose()


def _insert_task_ends(rows: Iterable[tuple]) -> None:
    """把成功结束写成已完成的运行记录。失败、取消和阶段事件不进入完成态。"""
    engine, session = _sync_session()
    try:
        for item in rows:
            project_id, flow, task_type, phase, outcome, duration_ms = item[:6]
            started_at = item[6] if len(item) > 6 else None
            if started_at is None:
                started_at = datetime.now(timezone.utc) - timedelta(milliseconds=max(int(duration_ms), 1))
            if phase == "end" and outcome == "success":
                status = "completed"
            elif phase == "end":
                status = "failed"
            else:
                status = "running"
            session.add(TaskRun(
                project_id=project_id,
                flow=flow,
                node_key=f"{task_type}-{time.time_ns()}",
                task_name=f"dash-{task_type}-{project_id}",
                status=status,
                attempt=1,
                duration_ms=duration_ms if status == "completed" else None,
                created_at=started_at,
            ))
        session.commit()
    finally:
        session.close()
        engine.dispose()


def _insert_documents(project_ids: Iterable[int]) -> None:
    engine, session = _sync_session()
    try:
        for project_id in project_ids:
            session.add(Document(
                project_id=project_id,
                doc_type=DocumentType.TENDER,
                filename="tender.pdf",
                file_path=f"dash/{project_id}/tender.pdf",
                file_size=1,
            ))
        session.commit()
    finally:
        session.close()
        engine.dispose()


def _insert_final_outputs(rows: Iterable[tuple[int, ProjectType, datetime]]) -> None:
    """写入各业务的最终产物，并把产出时间钉在 at。"""
    engine, session = _sync_session()
    try:
        for project_id, project_type, at in rows:
            if project_type == ProjectType.BID_WRITING:
                session.add(WritingRenderArtifact(
                    project_id=project_id,
                    render_version=1,
                    rendered_at=at,
                    created_at=at,
                    updated_at=at,
                ))
                continue
            if project_type == ProjectType.BID_REVIEW:
                document = Document(
                    project_id=project_id,
                    doc_type=DocumentType.TENDER,
                    filename="tender.pdf",
                    file_path=f"dash/{project_id}/tender.pdf",
                    file_size=1,
                    created_at=at,
                    updated_at=at,
                )
                session.add(document)
                session.flush()
                requirement = Requirement(
                    project_id=project_id,
                    document_id=document.id,
                    category=RequirementCategory.TECH,
                    description="dash duration fixture",
                    created_at=at,
                    updated_at=at,
                )
                session.add(requirement)
                session.flush()
                session.add(ReviewResult(
                    project_id=project_id,
                    requirement_id=requirement.id,
                    requirement_summary="dash duration fixture",
                    compliance_level=ComplianceLevel.FULLY,
                    score=1,
                    comment="ok",
                    created_at=at,
                    updated_at=at,
                ))
                continue
            left = Document(
                project_id=project_id,
                doc_type=DocumentType.BID,
                filename="a.pdf",
                file_path=f"dash/{project_id}/a.pdf",
                file_size=1,
                created_at=at,
                updated_at=at,
            )
            right = Document(
                project_id=project_id,
                doc_type=DocumentType.BID,
                filename="b.pdf",
                file_path=f"dash/{project_id}/b.pdf",
                file_size=1,
                created_at=at,
                updated_at=at,
            )
            session.add(left)
            session.add(right)
            session.flush()
            session.add(CollusionResult(
                project_id=project_id,
                doc_a_id=left.id,
                doc_b_id=right.id,
                created_at=at,
                updated_at=at,
            ))
        session.commit()
    finally:
        session.close()
        engine.dispose()


def _get_status_count(items: list[dict], key: str) -> int:
    return next(item["count"] for item in items if item["key"] == key)


def _get_type_count(items: list[dict], key: str) -> int:
    return next(item["count"] for item in items if item["key"] == key)


def test_dashboard_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/projects/dashboard")
    assert response.status_code == 401


def test_regular_user_dashboard_only_counts_own_projects(
    client: TestClient,
) -> None:
    auth_headers = _register_and_login(client, f"scope{time.time_ns()}")
    me_response = client.get("/api/auth/me", headers=auth_headers)
    assert me_response.status_code == 200, me_response.text
    owner_id = me_response.json()["data"]["id"]
    now = datetime.now(timezone.utc).replace(microsecond=0)

    own_id, _, _, other_id = _insert_projects([
        (f"dash-own-writing-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.WRITING, now - timedelta(days=2), now),
        (f"dash-own-review-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.COMPLETED, now - timedelta(days=1), now - timedelta(hours=1)),
        (f"dash-own-failed-{time.time_ns()}", owner_id, ProjectType.COLLUSION_CHECK, ProjectStatus.FAILED, now, now - timedelta(hours=2)),
        (f"dash-other-{time.time_ns()}", 1, ProjectType.BID_REVIEW, ProjectStatus.COMPLETED, now, now),
    ])

    response = client.get("/api/projects/dashboard?window_days=7", headers=auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["scope"] == "mine"
    assert data["summary"]["total"] == 3
    assert data["summary"]["processing"] == 1
    assert data["summary"]["completed"] == 1
    assert data["summary"]["failed"] == 1
    assert _get_type_count(data["type_distribution"], "bid_review") == 1
    assert _get_type_count(data["type_distribution"], "bid_writing") == 1
    assert _get_type_count(data["type_distribution"], "collusion_check") == 1
    assert all(project["id"] != other_id for project in data["recent_projects"])
    assert any(project["id"] == own_id for project in data["recent_projects"])


def test_admin_dashboard_counts_all_projects(client: TestClient, admin_headers: dict[str, str]) -> None:
    before_response = client.get("/api/projects/dashboard?window_days=7", headers=admin_headers)
    assert before_response.status_code == 200, before_response.text
    before_total = before_response.json()["data"]["summary"]["total"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _insert_projects([
        (f"dash-admin-a-{time.time_ns()}", 1, ProjectType.BID_REVIEW, ProjectStatus.COMPLETED, now, now - timedelta(minutes=2)),
        (f"dash-admin-b-{time.time_ns()}", 1, ProjectType.BID_WRITING, ProjectStatus.OUTLINING, now, now - timedelta(minutes=1)),
    ])

    response = client.get("/api/projects/dashboard?window_days=7", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["scope"] == "all"
    assert data["summary"]["total"] == before_total + 2


def test_dashboard_zero_fills_required_buckets_for_empty_user(client: TestClient) -> None:
    empty_headers = _register_and_login(client, "empty")

    response = client.get("/api/projects/dashboard?window_days=7", headers=empty_headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["summary"] == {
        "total": 0,
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "this_month": 0,
    }
    assert [item["key"] for item in data["type_distribution"]] == [
        "bid_writing",
        "bid_review",
        "collusion_check",
    ]
    assert [item["key"] for item in data["status_distribution"]] == [
        "pending",
        "processing",
        "completed",
        "failed",
    ]
    assert len(data["daily_created"]) == 7
    assert all(item["count"] == 0 for item in data["daily_created"])
    assert len(data["daily_completed"]) == 7
    assert all(item["count"] == 0 for item in data["daily_completed"])
    assert data["recent_projects"] == []
    assert data["health"]["success_rate"] is None
    assert data["health"]["backlog"] == 0
    assert [item["id"] for item in data["action_queue"]] == [
        "failed",
        "stalled",
        "awaiting_confirm",
        "awaiting_upload",
    ]
    assert [item["key"] for item in data["stage_groups"]] == [
        "awaiting_material",
        "document_handling",
        "awaiting_confirm",
        "ai_analysis",
        "result_generating",
    ]


def test_dashboard_daily_created_is_continuous_and_read_only(client: TestClient) -> None:
    headers = _register_and_login(client, "trend")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    login_payload = client.get("/api/projects", headers=headers)
    assert login_payload.status_code == 200
    owner_id = client.get("/api/auth/me", headers=headers).json()["data"]["id"]
    first_id, _ = _insert_projects([
        (f"dash-trend-a-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.REVIEWING, now - timedelta(days=6), now - timedelta(days=1)),
        (f"dash-trend-b-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.REVIEWING, now, now),
    ])

    before = client.get(f"/api/projects/{first_id}", headers=headers).json()["data"]
    response = client.get("/api/projects/dashboard?window_days=7", headers=headers)
    after = client.get(f"/api/projects/{first_id}", headers=headers).json()["data"]

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["daily_created"]) == 7
    assert sum(day["count"] for day in data["daily_created"]) >= 2
    assert before["status"] == after["status"]
    assert before["updated_at"] == after["updated_at"]
    assert _get_status_count(data["stage_distribution"], "reviewing") >= 2


def test_dashboard_response_does_not_expose_admin_ops_or_failure_details(
    client: TestClient,
    auth_headers: dict[str, str],
    registered_user: dict,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _insert_projects([
        (f"dash-forbidden-fields-{time.time_ns()}", registered_user["id"], ProjectType.BID_REVIEW, ProjectStatus.FAILED, now, now),
    ])

    response = client.get("/api/projects/dashboard", headers=auth_headers)
    assert response.status_code == 200, response.text
    payload = response.text

    for forbidden in [
        "owner_username",
        "system_errors",
        "workers",
        "cost",
        "throughput",
        "progress",
        "error_message",
    ]:
        assert forbidden not in payload


def test_dashboard_health_uses_window_and_keeps_confirm_gates_together(client: TestClient) -> None:
    headers = _register_and_login(client, "health")
    owner_id = client.get("/api/auth/me", headers=headers).json()["data"]["id"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    project_ids = _insert_projects([
        (f"dash-done-a-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.COMPLETED, now - timedelta(seconds=1600), now - timedelta(seconds=1000)),
        (f"dash-done-b-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.COMPLETED, now - timedelta(seconds=2200), now - timedelta(seconds=1000)),
        (f"dash-old-done-{time.time_ns()}", owner_id, ProjectType.COLLUSION_CHECK, ProjectStatus.COMPLETED, now - timedelta(days=40), now - timedelta(days=40)),
        (f"dash-failed-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.FAILED, now - timedelta(days=3), now - timedelta(hours=1)),
        (f"dash-empty-done-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.COMPLETED, now - timedelta(hours=3), now - timedelta(hours=2)),
        (f"dash-empty-fail-{time.time_ns()}", owner_id, ProjectType.COLLUSION_CHECK, ProjectStatus.FAILED, now - timedelta(hours=4), now - timedelta(hours=3)),
        (f"dash-stall-{time.time_ns()}", owner_id, ProjectType.COLLUSION_CHECK, ProjectStatus.PARSING, now - timedelta(days=3), now - timedelta(hours=5)),
        (f"dash-fresh-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.REVIEWING, now - timedelta(hours=1), now - timedelta(minutes=5)),
        (f"dash-outline-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.OUTLINED, now - timedelta(hours=1), now - timedelta(minutes=15)),
        (f"dash-match-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.MATCHED, now - timedelta(hours=1), now - timedelta(minutes=12)),
        (f"dash-upload-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.CREATED, now - timedelta(hours=2), now - timedelta(hours=2)),
    ])
    _insert_documents([project_ids[0], project_ids[1], project_ids[3]])

    response = client.get("/api/projects/dashboard?window_days=7", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    health = data["health"]
    actions = {item["id"]: item for item in data["action_queue"]}
    groups = {item["key"]: item for item in data["stage_groups"]}

    by_type = {item["project_type"]: item for item in health["completion_by_type"]}
    assert health["success_rate"] == 2 / 3
    assert by_type["bid_review"]["avg_duration_ms"] is None
    assert by_type["bid_writing"]["avg_duration_ms"] is None
    assert by_type["collusion_check"]["avg_duration_ms"] is None
    assert "median_seconds" not in by_type["bid_review"]
    assert "p95_completion_seconds" not in health
    assert health["backlog"] == 5
    assert actions["failed"]["count"] == 2
    assert actions["stalled"]["count"] == 1
    assert actions["stalled"]["filter"]["stale_hours"] == 2
    assert actions["awaiting_confirm"]["count"] == 2
    assert set(actions["awaiting_confirm"]["filter"]["stages"]) == {"extracted", "outlined", "matched"}
    assert actions["awaiting_upload"]["count"] == 1
    assert groups["awaiting_confirm"]["count"] == 2
    assert set(groups["awaiting_confirm"]["member_statuses"]) == {"extracted", "outlined", "matched"}
    assert groups["document_handling"]["count"] == 1
    assert len(data["daily_completed"]) == 7
    assert sum(day["count"] for day in data["daily_completed"]) == 3


def test_dashboard_duration_counts_only_successful_completed_runs(client: TestClient) -> None:
    headers = _register_and_login(client, "duration")
    owner_id = client.get("/api/auth/me", headers=headers).json()["data"]["id"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    writing_id, review_id, failed_id, stopped_id = _insert_projects([
        (f"dash-dur-writing-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.COMPLETED, now - timedelta(hours=2), now),
        (f"dash-dur-review-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.COMPLETED, now - timedelta(hours=2), now),
        (f"dash-dur-failed-{time.time_ns()}", owner_id, ProjectType.COLLUSION_CHECK, ProjectStatus.FAILED, now - timedelta(hours=2), now),
        (f"dash-dur-stopped-{time.time_ns()}", owner_id, ProjectType.COLLUSION_CHECK, ProjectStatus.REVIEWING, now - timedelta(hours=2), now),
    ])
    _insert_final_outputs([
        (writing_id, ProjectType.BID_WRITING, now),
        (review_id, ProjectType.BID_REVIEW, now),
    ])
    _insert_task_ends([
        (writing_id, "writer", "compose", "end", "success", 120_000),
        (writing_id, "writer", "compose", "end", "failure", 9_000_000),
        (writing_id, "process", "process", "stage", "success", 50_000),
        (review_id, "tender", "review", "end", "success", 200_000),
        (review_id, "tender", "extract", "end", "success", 256_000),
        (review_id, "tender", "review", "end", "cancelled", 1_000_000),
        (failed_id, "collusion", "analyze", "end", "success", 300_000),
        (stopped_id, "collusion", "analyze", "end", "success", 400_000),
    ])

    response = client.get("/api/projects/dashboard?window_days=7", headers=headers)
    assert response.status_code == 200, response.text
    by_type = {
        item["project_type"]: item
        for item in response.json()["data"]["health"]["completion_by_type"]
    }
    assert by_type["bid_writing"]["avg_duration_ms"] == 120_000
    assert by_type["bid_writing"]["sample_count"] == 1
    assert by_type["bid_review"]["avg_duration_ms"] == 456_000
    assert by_type["bid_review"]["sample_count"] == 1
    assert by_type["collusion_check"]["avg_duration_ms"] is None
    assert by_type["collusion_check"]["sample_count"] == 0


def test_dashboard_health_window_does_not_follow_trend_window(client: TestClient) -> None:
    headers = _register_and_login(client, "healthwin")
    owner_id = client.get("/api/auth/me", headers=headers).json()["data"]["id"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    project_ids = _insert_projects([
        (f"dash-hw-w1-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.COMPLETED, now - timedelta(days=1), now - timedelta(days=1)),
        (f"dash-hw-w2-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.COMPLETED, now - timedelta(days=20), now - timedelta(days=20)),
        (f"dash-hw-c1-{time.time_ns()}", owner_id, ProjectType.COLLUSION_CHECK, ProjectStatus.COMPLETED, now - timedelta(days=12), now - timedelta(days=12)),
        (f"dash-hw-open-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.CREATED, now - timedelta(days=40), now - timedelta(hours=1)),
        (f"dash-hw-mid-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.REVIEWING, now - timedelta(days=15), now - timedelta(hours=1)),
    ])
    writing_recent, writing_old, collusion_old = project_ids[:3]
    _insert_final_outputs([
        (writing_recent, ProjectType.BID_WRITING, now - timedelta(days=1)),
        (writing_old, ProjectType.BID_WRITING, now - timedelta(days=20)),
        (collusion_old, ProjectType.COLLUSION_CHECK, now - timedelta(days=12)),
    ])
    _insert_task_ends([
        (writing_recent, "writer", "compose", "end", "success", 60_000, now - timedelta(days=1)),
        (writing_old, "writer", "compose", "end", "success", 180_000, now - timedelta(days=20)),
        (collusion_old, "collusion", "analyze", "end", "success", 90_000, now - timedelta(days=12)),
    ])

    short = client.get("/api/projects/dashboard?window_days=90&health_window_days=7", headers=headers)
    long = client.get("/api/projects/dashboard?window_days=7&health_window_days=30", headers=headers)
    assert short.status_code == 200, short.text
    assert long.status_code == 200, long.text
    short_data = short.json()["data"]
    long_data = long.json()["data"]

    assert short_data["window_days"] == 90
    assert long_data["window_days"] == 7
    assert len(short_data["daily_completed"]) == 90
    assert len(long_data["daily_completed"]) == 7
    assert short_data["health_window_days"] == 7
    assert long_data["health_window_days"] == 30
    assert short_data["health"]["backlog"] == 2
    assert long_data["health"]["backlog"] == 2
    assert short_data["health"]["net_change"] == 0
    assert long_data["health"]["net_change"] == 1

    short_by_type = {item["project_type"]: item for item in short_data["health"]["completion_by_type"]}
    long_by_type = {item["project_type"]: item for item in long_data["health"]["completion_by_type"]}
    assert short_by_type["bid_writing"]["avg_duration_ms"] == 60_000
    assert short_by_type["collusion_check"]["avg_duration_ms"] is None
    assert long_by_type["bid_writing"]["avg_duration_ms"] == 120_000
    assert long_by_type["collusion_check"]["avg_duration_ms"] == 90_000


def test_duration_uses_result_time_and_drops_later_success(client: TestClient) -> None:
    headers = _register_and_login(client, "resultclk")
    owner_id = client.get("/api/auth/me", headers=headers).json()["data"]["id"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    review_id, writing_id = _insert_projects([
        (f"dash-clk-review-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.COMPLETED, now - timedelta(days=50), now),
        (f"dash-clk-writing-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.COMPLETED, now - timedelta(days=1), now),
    ])
    result_at = now - timedelta(days=40)
    _insert_final_outputs([(review_id, ProjectType.BID_REVIEW, result_at)])
    _insert_task_ends([
        (review_id, "tender", "review", "end", "success", 200_000, result_at - timedelta(minutes=8), result_at - timedelta(minutes=1)),
        (review_id, "tender", "review", "end", "success", 900_000, now - timedelta(minutes=20), now - timedelta(minutes=5)),
        (writing_id, "writer", "compose", "end", "success", 50_000, now - timedelta(minutes=10), now - timedelta(minutes=5)),
    ])

    short = client.get("/api/projects/dashboard?health_window_days=7", headers=headers)
    long = client.get("/api/projects/dashboard?health_window_days=90", headers=headers)
    assert short.status_code == 200, short.text
    assert long.status_code == 200, long.text
    short_by_type = {item["project_type"]: item for item in short.json()["data"]["health"]["completion_by_type"]}
    long_by_type = {item["project_type"]: item for item in long.json()["data"]["health"]["completion_by_type"]}

    assert short.json()["data"]["health"]["success_rate"] == 1
    assert short_by_type["bid_review"]["avg_duration_ms"] is None
    assert short_by_type["bid_writing"]["avg_duration_ms"] is None
    assert long_by_type["bid_review"]["avg_duration_ms"] == 200_000
    assert long_by_type["bid_review"]["sample_count"] == 1
    assert long_by_type["bid_writing"]["sample_count"] == 0


def test_duration_ignores_pruned_observations_for_writing_and_review(client: TestClient) -> None:
    headers = _register_and_login(client, "durable")
    owner_id = client.get("/api/auth/me", headers=headers).json()["data"]["id"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    writing_id, review_id = _insert_projects([
        (f"dash-keep-writing-{time.time_ns()}", owner_id, ProjectType.BID_WRITING, ProjectStatus.COMPLETED, now - timedelta(days=80), now - timedelta(days=80)),
        (f"dash-keep-review-{time.time_ns()}", owner_id, ProjectType.BID_REVIEW, ProjectStatus.COMPLETED, now - timedelta(days=70), now - timedelta(days=70)),
    ])
    _insert_final_outputs([
        (writing_id, ProjectType.BID_WRITING, now - timedelta(days=80)),
        (review_id, ProjectType.BID_REVIEW, now - timedelta(days=70)),
    ])
    _insert_task_ends([
        (writing_id, "writer", "compose", "end", "success", 114_000, now - timedelta(days=80)),
        (review_id, "tender", "review", "end", "success", 220_000, now - timedelta(days=70)),
    ])
    engine, session = _sync_session()
    try:
        for project_id, flow, task_type in (
            (writing_id, "writer", "compose"),
            (review_id, "tender", "review"),
        ):
            session.add(TaskObservation(
                task_id=f"dash-stale-{project_id}-{time.time_ns()}",
                flow=flow,
                task_type=task_type,
                phase="end",
                project_id=project_id,
                outcome="success",
                duration_ms=9_000_000,
            ))
        session.commit()
    finally:
        session.close()
        engine.dispose()

    response = client.get("/api/projects/dashboard?health_window_days=90", headers=headers)
    assert response.status_code == 200, response.text
    by_type = {
        item["project_type"]: item
        for item in response.json()["data"]["health"]["completion_by_type"]
    }
    assert by_type["bid_writing"]["avg_duration_ms"] == 114_000
    assert by_type["bid_writing"]["sample_count"] == 1
    assert by_type["bid_review"]["avg_duration_ms"] == 220_000
    assert by_type["bid_review"]["sample_count"] == 1
