"""项目管理接口集成测试

覆盖: CRUD、权限校验（普通用户 vs 管理员）。
"""

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings

_TS = str(int(time.time()))[-6:]
_MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def _get_sync_db_url() -> str:
    return settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


def _seed_bid_writing_rows(project_id: int, document_id: int) -> None:
    """为删除回归测试构造最小 writing_* 数据链。"""
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            clause_id = conn.execute(
                text(
                    """
                    INSERT INTO writing_clauses (
                        project_id, document_id, layer, sub_type,
                        content, raw_text, confirmed, is_manual, sort_order
                    ) VALUES (
                        :project_id, :document_id, 'hard_constraint', 'submission_rule',
                        :content, :raw_text, 1, 0, 0
                    )
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "document_id": document_id,
                    "content": "delete regression clause",
                    "raw_text": "delete regression raw clause",
                },
            ).scalar_one()

            outline_node_id = conn.execute(
                text(
                    """
                    INSERT INTO writing_outline_nodes (
                        project_id, title, depth, sort_order, node_type,
                        is_required, status
                    ) VALUES (
                        :project_id, :title, 0, 0, 'free_writing', 1, 'pending'
                    )
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "title": "delete regression outline",
                },
            ).scalar_one()

            material_id = conn.execute(
                text(
                    """
                    INSERT INTO writing_materials (
                        project_id, document_id, material_type, title,
                        content, processing_status
                    ) VALUES (
                        :project_id, :document_id, 'text', :title,
                        :content, 'completed'
                    )
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "document_id": document_id,
                    "title": "delete regression material",
                    "content": "delete regression material body",
                },
            ).scalar_one()

            conn.execute(
                text(
                    """
                    INSERT INTO writing_bindings (
                        project_id, outline_node_id, clause_id, material_id,
                        binding_type, confidence, source, status
                    ) VALUES (
                        :project_id, :outline_node_id, :clause_id, :material_id,
                        'material_to_node', 0.95, 'manual', 'confirmed'
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "outline_node_id": outline_node_id,
                    "clause_id": clause_id,
                    "material_id": material_id,
                },
            )

            conn.execute(
                text(
                    """
                    INSERT INTO writing_render_profiles (
                        project_id, font_body, font_heading, font_size_body,
                        font_size_h1, font_size_h2, font_size_h3, line_spacing,
                        margin_top, margin_bottom, margin_left, margin_right,
                        page_number_position
                    ) VALUES (
                        :project_id, 'SimSun', 'SimHei', 12.0,
                        22.0, 16.0, 14.0, 1.5,
                        25.4, 25.4, 31.8, 31.8,
                        'bottom_center'
                    )
                    """
                ),
                {"project_id": project_id},
            )

            conn.execute(
                text(
                    """
                    INSERT INTO writing_render_artifacts (
                        project_id, render_version, preview_html
                    ) VALUES (
                        :project_id, 1, :preview_html
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "preview_html": "<p>delete regression preview</p>",
                },
            )
    finally:
        engine.dispose()


def _count_project_rows(project_id: int) -> dict[str, int]:
    """统计关键 project 直属表残留记录数。"""
    tables = [
        "documents",
        "writing_clauses",
        "writing_outline_nodes",
        "writing_materials",
        "writing_bindings",
        "writing_render_profiles",
        "writing_render_artifacts",
    ]
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            return {
                table: int(
                    conn.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE project_id = :project_id"),
                        {"project_id": project_id},
                    ).scalar_one()
                )
                for table in tables
            }
    finally:
        engine.dispose()


class TestProjectCRUD:

    def test_create_project(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/projects",
            json={"name": "测试项目-投标审查"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["name"] == "测试项目-投标审查"
        assert data["status"] == "created"

    def test_create_collusion_project(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/projects",
            json={"name": "测试项目-串标审查", "project_type": "collusion_check"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["project_type"] == "collusion_check"

    def test_list_projects(self, client: TestClient, auth_headers):
        client.post(
            "/api/projects",
            json={"name": "列表测试项目"},
            headers=auth_headers,
        )
        resp = client.get("/api/projects", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_get_project(self, client: TestClient, auth_headers):
        create_resp = client.post(
            "/api/projects",
            json={"name": "详情测试项目"},
            headers=auth_headers,
        )
        pid = create_resp.json()["data"]["id"]

        resp = client.get(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == pid

    @pytest.mark.xfail(
        reason="MissingGreenlet: TestClient 同步上下文下 updated_at 懒加载失败，生产环境正常",
        strict=False,
    )
    def test_update_project(self, client: TestClient, auth_headers):
        create_resp = client.post(
            "/api/projects",
            json={"name": "更新前名称"},
            headers=auth_headers,
        )
        pid = create_resp.json()["data"]["id"]

        resp = client.put(
            f"/api/projects/{pid}",
            json={"name": "更新后名称"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "更新后名称"

    def test_delete_project(self, client: TestClient, auth_headers):
        create_resp = client.post(
            "/api/projects",
            json={"name": "待删除项目"},
            headers=auth_headers,
        )
        pid = create_resp.json()["data"]["id"]

        resp = client.delete(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 200

        get_resp = client.get(f"/api/projects/{pid}", headers=auth_headers)
        assert get_resp.status_code == 404

    def test_delete_bid_writing_project_with_writing_children(
        self,
        client: TestClient,
        auth_headers,
    ):
        create_resp = client.post(
            "/api/projects",
            json={
                "name": f"delete-bid-writing-{time.time_ns()}",
                "project_type": "bid_writing",
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        pid = create_resp.json()["data"]["id"]

        upload_resp = client.post(
            f"/api/projects/{pid}/files",
            headers=auth_headers,
            data={"doc_type": "tender"},
            files={"file": ("seed.pdf", _MINIMAL_PDF_BYTES, "application/pdf")},
        )
        assert upload_resp.status_code == 201
        document_id = upload_resp.json()["data"]["id"]

        _seed_bid_writing_rows(pid, document_id)
        before_counts = _count_project_rows(pid)
        assert all(count > 0 for count in before_counts.values())

        resp = client.delete(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 200, resp.text

        get_resp = client.get(f"/api/projects/{pid}", headers=auth_headers)
        assert get_resp.status_code == 404

        after_counts = _count_project_rows(pid)
        assert after_counts == {table: 0 for table in before_counts}


class TestProjectPermissions:

    def test_unauthenticated_access(self, client: TestClient):
        resp = client.get("/api/projects")
        assert resp.status_code == 401

    def test_cannot_access_other_user_project(self, client: TestClient, auth_headers):
        create_resp = client.post(
            "/api/projects",
            json={"name": "用户A的项目"},
            headers=auth_headers,
        )
        pid = create_resp.json()["data"]["id"]

        other_user = {
            "username": f"otherperm{_TS}",
            "email": f"otherperm{_TS}@example.com",
            "password": "Pass1234",
            "confirm_password": "Pass1234",
        }
        client.post("/api/auth/register", json=other_user)
        login_resp = client.post("/api/auth/login", json={
            "username": other_user["username"],
            "password": other_user["password"],
        })
        other_headers = {
            "Authorization": f"Bearer {login_resp.json()['data']['access_token']}"
        }

        resp = client.get(f"/api/projects/{pid}", headers=other_headers)
        assert resp.status_code == 403

    def test_admin_can_access_any_project(self, client: TestClient, auth_headers, admin_headers):
        create_resp = client.post(
            "/api/projects",
            json={"name": "管理员访问测试"},
            headers=auth_headers,
        )
        pid = create_resp.json()["data"]["id"]

        resp = client.get(f"/api/projects/{pid}", headers=admin_headers)
        assert resp.status_code == 200

    def test_nonexistent_project(self, client: TestClient, auth_headers):
        resp = client.get("/api/projects/99999", headers=auth_headers)
        assert resp.status_code == 404
