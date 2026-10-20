"""管理员在线用户统计接口测试"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.presence_service import (
    OnlinePresenceSummary,
    OnlinePresenceUsers,
    PresenceService,
    PresenceWriteResult,
)


def test_presence_heartbeat_marks_current_user(
    client: TestClient,
    auth_headers: dict[str, str],
    registered_user: dict,
    monkeypatch,
) -> None:
    seen_user_ids: list[int] = []

    async def fake_mark_user_seen(
        self: PresenceService,
        user_id: int,
    ) -> PresenceWriteResult:
        seen_user_ids.append(user_id)
        return PresenceWriteResult(
            ok=True,
            degraded=False,
            online_window_seconds=120,
        )

    monkeypatch.setattr(PresenceService, "mark_user_seen", fake_mark_user_seen)

    response = client.post("/api/presence/heartbeat", headers=auth_headers)

    assert response.status_code == 200
    assert seen_user_ids == [registered_user["id"]]
    assert response.json()["data"] == {
        "ok": True,
        "degraded": False,
        "online_window_seconds": 120,
    }


def test_online_stats_requires_admin(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get("/api/users/online-stats", headers=auth_headers)

    assert response.status_code == 403


def test_admin_online_stats_returns_presence_summary(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch,
) -> None:
    async def fake_get_online_summary(
        self: PresenceService,
        candidate_user_ids=None,
    ) -> OnlinePresenceSummary:
        assert candidate_user_ids is None
        return OnlinePresenceSummary(
            online_count=3,
            online_window_seconds=120,
            degraded=False,
        )

    monkeypatch.setattr(PresenceService, "get_online_summary", fake_get_online_summary)

    response = client.get("/api/users/online-stats", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "online_count": 3,
        "online_window_seconds": 120,
        "degraded": False,
    }


def test_user_list_decorates_current_page_online_state(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch,
) -> None:
    seen_candidate_ids: list[list[int]] = []

    async def fake_get_online_user_ids(
        self: PresenceService,
        candidate_user_ids=None,
    ) -> OnlinePresenceUsers:
        candidates = list(candidate_user_ids or [])
        seen_candidate_ids.append(candidates)
        return OnlinePresenceUsers(
            online_user_ids={candidates[0]} if candidates else set(),
            online_window_seconds=120,
            degraded=False,
        )

    monkeypatch.setattr(PresenceService, "get_online_user_ids", fake_get_online_user_ids)

    response = client.get("/api/users?page=1&page_size=5", headers=admin_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert seen_candidate_ids
    assert data["items"]
    assert data["items"][0]["is_online"] is True
    assert all("is_online" in item for item in data["items"])


def test_user_list_filters_online_users_before_pagination(
    client: TestClient,
    admin_headers: dict[str, str],
    registered_user: dict,
    monkeypatch,
) -> None:
    online_id = registered_user["id"]
    seen_candidates: list[object] = []

    async def fake_get_online_user_ids(
        self: PresenceService,
        candidate_user_ids=None,
    ) -> OnlinePresenceUsers:
        seen_candidates.append(candidate_user_ids)
        if candidate_user_ids is None:
            return OnlinePresenceUsers(
                online_user_ids={online_id},
                online_window_seconds=120,
                degraded=False,
            )
        return OnlinePresenceUsers(
            online_user_ids={uid for uid in candidate_user_ids if uid == online_id},
            online_window_seconds=120,
            degraded=False,
        )

    monkeypatch.setattr(PresenceService, "get_online_user_ids", fake_get_online_user_ids)

    unfiltered = client.get("/api/users?page=1&page_size=50", headers=admin_headers)
    assert unfiltered.status_code == 200
    unfiltered_data = unfiltered.json()["data"]
    unfiltered_ids = [item["id"] for item in unfiltered_data["items"]]
    assert online_id in unfiltered_ids
    assert unfiltered_data["total"] >= 2

    response = client.get(
        "/api/users?page=1&page_size=50&is_online=true",
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [online_id]
    assert data["items"][0]["is_online"] is True
    assert seen_candidates[-1] is None


def test_user_list_online_filter_returns_empty_when_nobody_online(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch,
) -> None:
    async def fake_get_online_user_ids(
        self: PresenceService,
        candidate_user_ids=None,
    ) -> OnlinePresenceUsers:
        return OnlinePresenceUsers(
            online_user_ids=set(),
            online_window_seconds=120,
            degraded=False,
        )

    monkeypatch.setattr(PresenceService, "get_online_user_ids", fake_get_online_user_ids)

    response = client.get("/api/users?is_online=true", headers=admin_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0
