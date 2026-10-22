"""错误日志管理接口集成测试。

 起本文件依赖 system_error_logs 的 traffic_class / occurrence_count 两列,
需要在 alembic 迁移 c9d3e7a52f61 执行之后才能通过。
"""

import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings

# 允许在种子数据里指定的列; 未给出的列走数据库默认值
_SEED_COLUMNS = (
    "level", "message", "source", "resolved", "module",
    "traffic_class", "project_id", "incident_id",
    "message_fingerprint", "occurrence_count",
)


def _get_sync_db_url() -> str:
    return settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


def _seed_error_logs(records: list[dict]) -> list[int]:
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            ids: list[int] = []
            for record in records:
                # 默认 production: 用例造的数据要能出现在"默认只看生产流量"的列表里
                payload = {"traffic_class": "production", **record}
                columns = [c for c in _SEED_COLUMNS if c in payload]
                placeholders = ", ".join(f":{c}" for c in columns)
                log_id = conn.execute(
                    text(
                        f"INSERT INTO system_error_logs ({', '.join(columns)}) "
                        f"VALUES ({placeholders}) RETURNING id"
                    ),
                    payload,
                ).scalar_one()
                ids.append(int(log_id))
            return ids
    finally:
        engine.dispose()


def _fetch_log_states(log_ids: list[int]) -> dict[int, bool]:
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, resolved
                    FROM system_error_logs
                    WHERE id = ANY(:log_ids)
                    """
                ),
                {"log_ids": log_ids},
            ).all()
            return {int(row[0]): bool(row[1]) for row in rows}
    finally:
        engine.dispose()


def _cleanup_logs(log_ids: list[int]) -> None:
    if not log_ids:
        return
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM system_error_logs WHERE id = ANY(:log_ids)"),
                {"log_ids": log_ids},
            )
    finally:
        engine.dispose()


class TestErrorLogBatchActions:

    def test_batch_resolve_supports_select_all_with_filters_and_exclusions(
        self,
        client: TestClient,
        admin_headers: dict,
    ):
        keyword = f"error-log-resolve-{time.time_ns()}"
        log_ids = _seed_error_logs([
            {"level": "ERROR", "message": f"{keyword}-a", "source": "backend", "resolved": False},
            {"level": "ERROR", "message": f"{keyword}-b", "source": "backend", "resolved": False},
            {"level": "ERROR", "message": f"{keyword}-c", "source": "backend", "resolved": True},
            {"level": "WARNING", "message": f"{keyword}-d", "source": "backend", "resolved": False},
        ])
        try:
            resp = client.post(
                "/api/admin/error-logs/batch-resolve",
                headers=admin_headers,
                json={
                    "select_all": True,
                    "exclude_ids": [log_ids[1]],
                    "filters": {
                        "level": "ERROR",
                        "keyword": keyword,
                    },
                },
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["resolved"] == 1
            assert data["already_resolved"] == 1
            assert data["excluded"] == 1

            states = _fetch_log_states(log_ids)
            assert states[log_ids[0]] is True
            assert states[log_ids[1]] is False
            assert states[log_ids[2]] is True
            assert states[log_ids[3]] is False
        finally:
            _cleanup_logs(log_ids)

    def test_batch_delete_supports_select_all_with_filters_and_exclusions(
        self,
        client: TestClient,
        admin_headers: dict,
    ):
        keyword = f"error-log-delete-{time.time_ns()}"
        log_ids = _seed_error_logs([
            {"level": "ERROR", "message": f"{keyword}-a", "source": "backend", "resolved": False},
            {"level": "ERROR", "message": f"{keyword}-b", "source": "backend", "resolved": False},
            {"level": "ERROR", "message": f"{keyword}-c", "source": "worker", "resolved": False},
        ])
        try:
            resp = client.post(
                "/api/admin/error-logs/batch-delete",
                headers=admin_headers,
                json={
                    "select_all": True,
                    "exclude_ids": [log_ids[1]],
                    "filters": {
                        "keyword": keyword,
                        "source": "backend",
                    },
                },
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["deleted"] == 1
            assert data["excluded"] == 1

            states = _fetch_log_states(log_ids)
            assert log_ids[0] not in states
            assert states[log_ids[1]] is False
            assert states[log_ids[2]] is False
        finally:
            _cleanup_logs(log_ids)


def _list_messages(
    client: TestClient, headers: dict, keyword: str, **params,
) -> list[str]:
    resp = client.get(
        "/api/admin/error-logs",
        headers=headers,
        params={"keyword": keyword, "page_size": 50, **params},
    )
    assert resp.status_code == 200, resp.text
    return [item["message"] for item in resp.json()["data"]["items"]]


class TestErrorLogTrafficIsolation:
    """: 测试/探测流量与生产流量在错误日志中必须彻底隔离。"""

    def test_list_defaults_to_production_only(
        self, client: TestClient, admin_headers: dict,
    ):
        keyword = f"traffic-isolation-{time.time_ns()}"
        log_ids = _seed_error_logs([
            {"level": "ERROR", "message": f"{keyword}-prod", "source": "backend",
             "resolved": False, "traffic_class": "production"},
            {"level": "ERROR", "message": f"{keyword}-regression", "source": "backend",
             "resolved": False, "traffic_class": "regression"},
            {"level": "ERROR", "message": f"{keyword}-probe", "source": "backend",
             "resolved": False, "traffic_class": "admin_test"},
        ])
        try:
            assert _list_messages(client, admin_headers, keyword) == [f"{keyword}-prod"]
            assert _list_messages(
                client, admin_headers, keyword, traffic_class="regression",
            ) == [f"{keyword}-regression"]
            assert _list_messages(
                client, admin_headers, keyword, traffic_class="admin_test",
            ) == [f"{keyword}-probe"]
            assert len(_list_messages(
                client, admin_headers, keyword, traffic_class="all",
            )) == 3
            # 参数拼错时退回 production, 不得意外放开噪声
            assert _list_messages(
                client, admin_headers, keyword, traffic_class="bogus",
            ) == [f"{keyword}-prod"]
        finally:
            _cleanup_logs(log_ids)

    def test_legacy_unknown_rows_are_hidden_by_default(
        self, client: TestClient, admin_headers: dict,
    ):
        """存量数据标 unknown 且默认不展示: 其真实来源不可回溯, 默认放行等于没治理。

        锁定"挡掉"这一决策; 需要排查历史问题时用 traffic_class=unknown/all 显式查看。
        """
        keyword = f"legacy-unknown-{time.time_ns()}"
        log_ids = _seed_error_logs([
            {"level": "ERROR", "message": f"{keyword}-legacy", "source": "backend",
             "resolved": False, "traffic_class": "unknown"},
        ])
        try:
            assert _list_messages(client, admin_headers, keyword) == []
            assert _list_messages(
                client, admin_headers, keyword, traffic_class="unknown",
            ) == [f"{keyword}-legacy"]
            assert _list_messages(
                client, admin_headers, keyword, traffic_class="all",
            ) == [f"{keyword}-legacy"]
        finally:
            _cleanup_logs(log_ids)

    def test_production_incident_and_project_stay_traceable(
        self, client: TestClient, admin_headers: dict,
    ):
        """加了流量过滤之后, 真实故障的 project_id / incident_id 仍必须可追踪。"""
        keyword = f"traceable-{time.time_ns()}"
        incident = f"iid{time.time_ns() % 10 ** 12}"
        log_ids = _seed_error_logs([
            {"level": "ERROR", "message": f"{keyword}-root", "source": "backend",
             "resolved": False, "traffic_class": "production",
             "project_id": 987654, "incident_id": incident, "occurrence_count": 7},
        ])
        try:
            resp = client.get(
                "/api/admin/error-logs",
                headers=admin_headers,
                params={"incident_id": incident},
            )
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            assert len(items) == 1
            item = items[0]
            assert item["project_id"] == 987654
            assert item["incident_id"] == incident
            assert item["traffic_class"] == "production"
            assert item["occurrence_count"] == 7
        finally:
            _cleanup_logs(log_ids)

    def test_grouped_count_sums_occurrence_count(
        self, client: TestClient, admin_headers: dict,
    ):
        """折叠之后行数不再等于发生次数, 分组视图必须按 occurrence_count 求和。"""
        keyword = f"grouped-count-{time.time_ns()}"
        fingerprint = f"{time.time_ns():032x}"[:32]
        log_ids = _seed_error_logs([
            {"level": "ERROR", "message": f"{keyword}-a", "source": "backend",
             "resolved": False, "message_fingerprint": fingerprint,
             "occurrence_count": 1},
            {"level": "ERROR", "message": f"{keyword}-b", "source": "backend",
             "resolved": False, "message_fingerprint": fingerprint,
             "occurrence_count": 99},
        ])
        try:
            resp = client.get(
                "/api/admin/error-logs/grouped",
                headers=admin_headers,
                params={"keyword": keyword, "page_size": 50},
            )
            assert resp.status_code == 200, resp.text
            groups = [
                g for g in resp.json()["data"]["groups"]
                if g["fingerprint"] == fingerprint
            ]
            assert len(groups) == 1
            assert groups[0]["count"] == 100
        finally:
            _cleanup_logs(log_ids)

    def test_stats_ignore_regression_traffic(
        self, client: TestClient, admin_headers: dict,
    ):
        """运维要能用条数判断生产可靠性: 5 条回归噪声不得计入统计, 1 条生产错误必须计入。"""
        def _error_total() -> int:
            resp = client.get(
                "/api/admin/error-logs/stats",
                headers=admin_headers,
                params={"hours": 1},
            )
            assert resp.status_code == 200, resp.text
            return int(resp.json()["data"]["by_level"].get("ERROR", 0))

        keyword = f"stats-isolation-{time.time_ns()}"
        before = _error_total()
        log_ids = _seed_error_logs([
            {"level": "ERROR", "message": f"{keyword}-prod", "source": "backend",
             "resolved": False, "traffic_class": "production"},
        ] + [
            {"level": "ERROR", "message": f"{keyword}-noise-{i}", "source": "backend",
             "resolved": False, "traffic_class": "regression"}
            for i in range(5)
        ])
        try:
            assert _error_total() - before == 1
        finally:
            _cleanup_logs(log_ids)

    def test_batch_delete_select_all_spares_other_traffic_classes(
        self, client: TestClient, admin_headers: dict,
    ):
        """按筛选结果全选必须与列表口径一致, 不得删掉界面上看不见的行。"""
        keyword = f"batch-traffic-{time.time_ns()}"
        log_ids = _seed_error_logs([
            {"level": "ERROR", "message": f"{keyword}-prod", "source": "backend",
             "resolved": False, "traffic_class": "production"},
            {"level": "ERROR", "message": f"{keyword}-regression", "source": "backend",
             "resolved": False, "traffic_class": "regression"},
        ])
        try:
            resp = client.post(
                "/api/admin/error-logs/batch-delete",
                headers=admin_headers,
                json={"select_all": True, "filters": {"keyword": keyword}},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["deleted"] == 1

            states = _fetch_log_states(log_ids)
            assert log_ids[0] not in states
            assert log_ids[1] in states
        finally:
            _cleanup_logs(log_ids)
