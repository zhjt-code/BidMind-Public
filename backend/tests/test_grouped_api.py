"""分组API回归测试 - 验证 /grouped 端点、fingerprint 筛选、按指纹批量操作。"""

import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.message_fingerprint import compute_fingerprint


def _sync_url() -> str:
    return settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


def _seed(records: list[dict]) -> list[int]:
    engine = create_engine(_sync_url())
    try:
        with engine.begin() as conn:
            ids: list[int] = []
            for r in records:
                lid = conn.execute(text(
                    "INSERT INTO system_error_logs "
                    "(level, message, source, resolved, module, message_fingerprint, category) "
                    "VALUES (:level, :message, :source, :resolved, :module, :message_fingerprint, :category) "
                    "RETURNING id"
                ), r).scalar_one()
                ids.append(int(lid))
            return ids
    finally:
        engine.dispose()


def _cleanup(ids: list[int]) -> None:
    if not ids:
        return
    engine = create_engine(_sync_url())
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM system_error_logs WHERE id = ANY(:ids)"
            ), {"ids": ids})
    finally:
        engine.dispose()


class TestGroupedAPI:

    def test_grouped_endpoint_returns_correct_groups(
        self, client: TestClient, admin_headers: dict,
    ):
        """GET /grouped 返回正确的分组聚合结果"""
        tag = str(time.time_ns())
        fp_a = compute_fingerprint("test.mod.a", "WARNING", f"GrpTest probe {tag}_1")
        fp_b = compute_fingerprint("test.mod.b", "ERROR", f"GrpTest timeout {tag}_1")

        log_ids = _seed([
            {"level": "WARNING", "message": f"GrpTest probe {tag}_1", "source": "backend",
             "resolved": False, "module": "test.mod.a", "message_fingerprint": fp_a, "category": "system"},
            {"level": "WARNING", "message": f"GrpTest probe {tag}_2", "source": "backend",
             "resolved": False, "module": "test.mod.a", "message_fingerprint": fp_a, "category": "system"},
            {"level": "WARNING", "message": f"GrpTest probe {tag}_3", "source": "backend",
             "resolved": True, "module": "test.mod.a", "message_fingerprint": fp_a, "category": "system"},
            {"level": "ERROR", "message": f"GrpTest timeout {tag}_1", "source": "backend",
             "resolved": False, "module": "test.mod.b", "message_fingerprint": fp_b, "category": "system"},
            {"level": "ERROR", "message": f"GrpTest timeout {tag}_2", "source": "backend",
             "resolved": False, "module": "test.mod.b", "message_fingerprint": fp_b, "category": "system"},
        ])
        try:
            resp = client.get("/api/admin/error-logs/grouped", headers=admin_headers, params={
                "keyword": f"GrpTest", "page_size": 50,
            })
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            fps = {g["fingerprint"]: g for g in data["groups"]}

            assert fp_a in fps, f"fp_a missing from groups"
            assert fp_b in fps, f"fp_b missing from groups"
            assert fps[fp_a]["count"] == 3
            assert fps[fp_a]["unresolved_count"] == 2
            assert fps[fp_b]["count"] == 2
            assert fps[fp_b]["unresolved_count"] == 2
            assert fps[fp_a]["sample_message"] != ""
            assert fps[fp_a]["latest_at"] is not None
        finally:
            _cleanup(log_ids)

    def test_fingerprint_filter_on_list_endpoint(
        self, client: TestClient, admin_headers: dict,
    ):
        """GET / 增加 fingerprint 参数后只返回该组日志"""
        tag = str(time.time_ns())
        fp = compute_fingerprint("test.fp", "ERROR", f"FpFilter {tag}_1")

        log_ids = _seed([
            {"level": "ERROR", "message": f"FpFilter {tag}_1", "source": "backend",
             "resolved": False, "module": "test.fp", "message_fingerprint": fp, "category": "system"},
            {"level": "ERROR", "message": f"FpFilter {tag}_2", "source": "backend",
             "resolved": False, "module": "test.fp", "message_fingerprint": fp, "category": "system"},
        ])
        try:
            resp = client.get("/api/admin/error-logs", headers=admin_headers, params={
                "fingerprint": fp, "keyword": "FpFilter", "page_size": 50,
            })
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            assert len(items) == 2
            for item in items:
                assert "FpFilter" in item["message"]
        finally:
            _cleanup(log_ids)

    def test_batch_resolve_by_fingerprints(
        self, client: TestClient, admin_headers: dict,
    ):
        """批量标记处理支持 fingerprints 参数"""
        tag = str(time.time_ns())
        fp = compute_fingerprint("test.batch", "WARNING", f"BatchFp {tag}_1")

        log_ids = _seed([
            {"level": "WARNING", "message": f"BatchFp {tag}_1", "source": "backend",
             "resolved": False, "module": "test.batch", "message_fingerprint": fp, "category": "system"},
            {"level": "WARNING", "message": f"BatchFp {tag}_2", "source": "backend",
             "resolved": False, "module": "test.batch", "message_fingerprint": fp, "category": "system"},
        ])
        try:
            resp = client.post("/api/admin/error-logs/batch-resolve", headers=admin_headers, json={
                "filters": {"fingerprints": [fp], "keyword": "BatchFp"},
            })
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["resolved"] == 2
        finally:
            _cleanup(log_ids)

    def test_batch_delete_by_fingerprints(
        self, client: TestClient, admin_headers: dict,
    ):
        """批量删除支持 fingerprints 参数"""
        tag = str(time.time_ns())
        fp = compute_fingerprint("test.del", "ERROR", f"DelFp {tag}_1")

        log_ids = _seed([
            {"level": "ERROR", "message": f"DelFp {tag}_1", "source": "backend",
             "resolved": False, "module": "test.del", "message_fingerprint": fp, "category": "system"},
            {"level": "ERROR", "message": f"DelFp {tag}_2", "source": "backend",
             "resolved": False, "module": "test.del", "message_fingerprint": fp, "category": "system"},
            {"level": "ERROR", "message": f"DelFp {tag}_3", "source": "backend",
             "resolved": False, "module": "test.del", "message_fingerprint": fp, "category": "system"},
        ])
        try:
            resp = client.post("/api/admin/error-logs/batch-delete", headers=admin_headers, json={
                "filters": {"fingerprints": [fp], "keyword": "DelFp"},
            })
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["deleted"] == 3
            log_ids.clear()
        finally:
            _cleanup(log_ids)
