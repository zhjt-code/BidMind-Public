"""标书编写目录删除接口回归测试"""

import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings

_MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def _get_sync_db_url() -> str:
    return settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


def _seed_outline_delete_rows(project_id: int, document_id: int) -> dict[str, int]:
    """插入目录删除回归测试所需的最小 writer 数据。"""
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            parent_id = conn.execute(
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
                    "title": "outline delete parent",
                },
            ).scalar_one()

            child_id = conn.execute(
                text(
                    """
                    INSERT INTO writing_outline_nodes (
                        project_id, parent_id, title, depth, sort_order, node_type,
                        is_required, status
                    ) VALUES (
                        :project_id, :parent_id, :title, 1, 0, 'free_writing', 1, 'pending'
                    )
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "parent_id": parent_id,
                    "title": "outline delete child",
                },
            ).scalar_one()

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
                    "content": "outline delete regression clause",
                    "raw_text": "outline delete regression raw clause",
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
                    "title": "outline delete regression material",
                    "content": "outline delete regression material body",
                },
            ).scalar_one()

            binding_id = conn.execute(
                text(
                    """
                    INSERT INTO writing_bindings (
                        project_id, outline_node_id, clause_id, material_id,
                        binding_type, confidence, source, status
                    ) VALUES (
                        :project_id, :outline_node_id, :clause_id, :material_id,
                        'material_to_node', 0.95, 'manual', 'confirmed'
                    )
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "outline_node_id": child_id,
                    "clause_id": clause_id,
                    "material_id": material_id,
                },
            ).scalar_one()

            draft_id = conn.execute(
                text(
                    """
                    INSERT INTO writing_section_drafts (
                        outline_node_id, version, content, content_format,
                        is_current, is_user_edited, is_locked, word_count, status
                    ) VALUES (
                        :outline_node_id, 1, :content, 'html',
                        1, 0, 0, 8, 'draft'
                    )
                    RETURNING id
                    """
                ),
                {
                    "outline_node_id": child_id,
                    "content": "<p>outline delete regression draft</p>",
                },
            ).scalar_one()

            return {
                "parent_id": int(parent_id),
                "child_id": int(child_id),
                "binding_id": int(binding_id),
                "draft_id": int(draft_id),
            }
    finally:
        engine.dispose()


def _seed_outline_update_row(project_id: int) -> int:
    """插入目录更新回归测试所需的单个目录节点。"""
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            node_id = conn.execute(
                text(
                    """
                    INSERT INTO writing_outline_nodes (
                        project_id, title, depth, sort_order, node_type,
                        is_required, status, target_word_count
                    ) VALUES (
                        :project_id, :title, 0, 0, 'data_fill', 1, 'pending', 500
                    )
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "title": "outline update target count",
                },
            ).scalar_one()
            return int(node_id)
    finally:
        engine.dispose()


def _seed_outline_create_parent(project_id: int) -> int:
    """插入目录创建接口测试所需的父节点与一个已有同级子节点。"""
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            parent_id = conn.execute(
                text(
                    """
                    INSERT INTO writing_outline_nodes (
                        project_id, title, depth, sort_order, node_type,
                        is_required, status, volume_type
                    ) VALUES (
                        :project_id, :title, 0, 0, 'free_writing',
                        1, 'pending', 'technical'
                    )
                    RETURNING id
                    """
                ),
                {"project_id": project_id, "title": "技术标"},
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO writing_outline_nodes (
                        project_id, parent_id, title, depth, sort_order, node_type,
                        is_required, status, volume_type
                    ) VALUES (
                        :project_id, :parent_id, :title, 1, 0, 'data_fill',
                        1, 'pending', 'technical'
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "parent_id": parent_id,
                    "title": "既有子节点",
                },
            )
            return int(parent_id)
    finally:
        engine.dispose()


def _fetch_outline_node_row(node_id: int) -> dict[str, object]:
    """读取目录节点关键字段，供创建接口断言。"""
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        title, parent_id, depth, sort_order, node_type,
                        target_word_count, volume_type, is_skeleton, status
                    FROM writing_outline_nodes
                    WHERE id = :node_id
                    """
                ),
                {"node_id": node_id},
            ).mappings().one()
            return dict(row)
    finally:
        engine.dispose()


def _fetch_outline_child_rows(parent_id: int) -> list[dict[str, object]]:
    """按排序读取父节点下的直接子节点。"""
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, title, sort_order
                    FROM writing_outline_nodes
                    WHERE parent_id = :parent_id
                    ORDER BY sort_order ASC, id ASC
                    """
                ),
                {"parent_id": parent_id},
            ).mappings().all()
            return [dict(row) for row in rows]
    finally:
        engine.dispose()


def _seed_outline_child(
    project_id: int, parent_id: int, title: str, sort_order: int,
) -> int:
    """向指定父节点追加一个直接子节点，供插入排序测试使用。"""
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            node_id = conn.execute(
                text(
                    """
                    INSERT INTO writing_outline_nodes (
                        project_id, parent_id, title, depth, sort_order, node_type,
                        is_required, status, volume_type
                    ) VALUES (
                        :project_id, :parent_id, :title, 1, :sort_order,
                        'data_fill', 1, 'pending', 'technical'
                    )
                    RETURNING id
                    """
                ),
                {
                    "project_id": project_id,
                    "parent_id": parent_id,
                    "title": title,
                    "sort_order": sort_order,
                },
            ).scalar_one()
            return int(node_id)
    finally:
        engine.dispose()


def _fetch_outline_delete_state(project_id: int, parent_id: int, child_id: int) -> dict[str, int]:
    """统计删除前后的目录、草稿和绑定状态。"""
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            return {
                "project_node_count": int(
                    conn.execute(
                        text(
                            "SELECT COUNT(*) FROM writing_outline_nodes WHERE project_id = :project_id"
                        ),
                        {"project_id": project_id},
                    ).scalar_one()
                ),
                "parent_exists": int(
                    conn.execute(
                        text("SELECT COUNT(*) FROM writing_outline_nodes WHERE id = :node_id"),
                        {"node_id": parent_id},
                    ).scalar_one()
                ),
                "child_exists": int(
                    conn.execute(
                        text("SELECT COUNT(*) FROM writing_outline_nodes WHERE id = :node_id"),
                        {"node_id": child_id},
                    ).scalar_one()
                ),
                "child_draft_count": int(
                    conn.execute(
                        text(
                            "SELECT COUNT(*) FROM writing_section_drafts WHERE outline_node_id = :node_id"
                        ),
                        {"node_id": child_id},
                    ).scalar_one()
                ),
                "child_binding_count": int(
                    conn.execute(
                        text(
                            "SELECT COUNT(*) FROM writing_bindings WHERE project_id = :project_id AND outline_node_id = :node_id"
                        ),
                        {"project_id": project_id, "node_id": child_id},
                    ).scalar_one()
                ),
                "null_outline_binding_count": int(
                    conn.execute(
                        text(
                            "SELECT COUNT(*) FROM writing_bindings WHERE project_id = :project_id AND outline_node_id IS NULL"
                        ),
                        {"project_id": project_id},
                    ).scalar_one()
                ),
            }
    finally:
        engine.dispose()


def _fetch_outline_target_word_count(node_id: int) -> int | None:
    """读取目录节点目标字数，供更新接口回归断言。"""
    engine = create_engine(_get_sync_db_url())
    try:
        with engine.begin() as conn:
            return conn.execute(
                text(
                    """
                    SELECT target_word_count
                    FROM writing_outline_nodes
                    WHERE id = :node_id
                    """
                ),
                {"node_id": node_id},
            ).scalar_one()
    finally:
        engine.dispose()


def _create_bid_writing_project_with_document(
    client: TestClient, auth_headers: dict[str, str],
) -> tuple[int, int]:
    """创建最小标书编写项目并上传一个招标文件。"""
    create_resp = client.post(
        "/api/projects",
        json={
            "name": f"outline-delete-{time.time_ns()}",
            "project_type": "bid_writing",
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    project_id = create_resp.json()["data"]["id"]

    upload_resp = client.post(
        f"/api/projects/{project_id}/files",
        headers=auth_headers,
        data={"doc_type": "tender"},
        files={"file": ("outline-seed.pdf", _MINIMAL_PDF_BYTES, "application/pdf")},
    )
    assert upload_resp.status_code == 201, upload_resp.text
    document_id = upload_resp.json()["data"]["id"]
    return project_id, document_id


class TestWritingOutlineDeleteAPI:
    def test_delete_leaf_outline_node_cascades_drafts_and_bindings(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, document_id = _create_bid_writing_project_with_document(
            client, auth_headers
        )
        seeded = _seed_outline_delete_rows(project_id, document_id)
        before_state = _fetch_outline_delete_state(
            project_id,
            seeded["parent_id"],
            seeded["child_id"],
        )
        assert before_state == {
            "project_node_count": 2,
            "parent_exists": 1,
            "child_exists": 1,
            "child_draft_count": 1,
            "child_binding_count": 1,
            "null_outline_binding_count": 0,
        }

        resp = client.delete(
            f"/api/writing/projects/{project_id}/outline/{seeded['child_id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 204, resp.text

        after_state = _fetch_outline_delete_state(
            project_id,
            seeded["parent_id"],
            seeded["child_id"],
        )
        assert after_state == {
            "project_node_count": 1,
            "parent_exists": 1,
            "child_exists": 0,
            "child_draft_count": 0,
            "child_binding_count": 0,
            "null_outline_binding_count": 0,
        }

    def test_delete_parent_outline_node_cascades_child_subtree(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, document_id = _create_bid_writing_project_with_document(
            client, auth_headers
        )
        seeded = _seed_outline_delete_rows(project_id, document_id)

        resp = client.delete(
            f"/api/writing/projects/{project_id}/outline/{seeded['parent_id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 204, resp.text

        after_state = _fetch_outline_delete_state(
            project_id,
            seeded["parent_id"],
            seeded["child_id"],
        )
        assert after_state == {
            "project_node_count": 0,
            "parent_exists": 0,
            "child_exists": 0,
            "child_draft_count": 0,
            "child_binding_count": 0,
            "null_outline_binding_count": 0,
        }


class TestWritingOutlineUpdateAPI:
    def test_update_outline_node_persists_target_word_count(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, document_id = _create_bid_writing_project_with_document(
            client, auth_headers
        )
        assert document_id > 0
        node_id = _seed_outline_update_row(project_id)

        resp = client.put(
            f"/api/writing/projects/{project_id}/outline/{node_id}",
            headers=auth_headers,
            json={"target_word_count": 900},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == node_id
        assert body["target_word_count"] == 900
        assert _fetch_outline_target_word_count(node_id) == 900

    def test_update_outline_node_rejects_invalid_target_word_count(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, document_id = _create_bid_writing_project_with_document(
            client, auth_headers
        )
        assert document_id > 0
        node_id = _seed_outline_update_row(project_id)

        resp = client.put(
            f"/api/writing/projects/{project_id}/outline/{node_id}",
            headers=auth_headers,
            json={"target_word_count": 50},
        )
        assert resp.status_code == 422, resp.text

    def test_update_outline_node_rejects_data_fill_target_word_count_above_type_limit(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, document_id = _create_bid_writing_project_with_document(
            client, auth_headers
        )
        assert document_id > 0
        node_id = _seed_outline_update_row(project_id)

        resp = client.put(
            f"/api/writing/projects/{project_id}/outline/{node_id}",
            headers=auth_headers,
            json={"target_word_count": 4000},
        )
        assert resp.status_code == 400, resp.text


class TestWritingOutlineReorderAPI:
    def test_reorder_outline_preserves_downstream_node_links(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, document_id = _create_bid_writing_project_with_document(
            client, auth_headers
        )
        seeded = _seed_outline_delete_rows(project_id, document_id)
        first_id = _seed_outline_child(project_id, seeded["parent_id"], "前置同级节点", 1)
        last_id = _seed_outline_child(project_id, seeded["parent_id"], "后置同级节点", 2)

        resp = client.post(
            f"/api/writing/projects/{project_id}/outline/reorder",
            headers=auth_headers,
            json={
                "parent_id": seeded["parent_id"],
                "node_ids": [last_id, seeded["child_id"], first_id],
            },
        )

        assert resp.status_code == 204, resp.text
        child_rows = _fetch_outline_child_rows(seeded["parent_id"])
        assert [(row["id"], row["sort_order"]) for row in child_rows] == [
            (last_id, 0),
            (seeded["child_id"], 1),
            (first_id, 2),
        ]
        state = _fetch_outline_delete_state(
            project_id,
            seeded["parent_id"],
            seeded["child_id"],
        )
        assert state["child_exists"] == 1
        assert state["child_draft_count"] == 1
        assert state["child_binding_count"] == 1
        assert state["null_outline_binding_count"] == 0

    def test_reorder_outline_rejects_incomplete_sibling_list(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, _ = _create_bid_writing_project_with_document(client, auth_headers)
        parent_id = _seed_outline_create_parent(project_id)
        second_id = _seed_outline_child(project_id, parent_id, "第二个同级节点", 1)

        resp = client.post(
            f"/api/writing/projects/{project_id}/outline/reorder",
            headers=auth_headers,
            json={
                "parent_id": parent_id,
                "node_ids": [second_id],
            },
        )

        assert resp.status_code == 400, resp.text


class TestWritingOutlineCreateAPI:
    def test_create_outline_node_persists_child_for_downstream_consumption(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, document_id = _create_bid_writing_project_with_document(
            client, auth_headers
        )
        assert document_id > 0
        parent_id = _seed_outline_create_parent(project_id)

        resp = client.post(
            f"/api/writing/projects/{project_id}/outline",
            headers=auth_headers,
            json={
                "parent_id": parent_id,
                "title": "人工新增施工方案",
                "node_type": "free_writing",
                "target_word_count": 1200,
            },
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "人工新增施工方案"
        assert body["parent_id"] == parent_id
        assert body["depth"] == 1
        assert body["sort_order"] == 1
        assert body["node_type"] == "free_writing"
        assert body["target_word_count"] == 1200
        assert body["volume_type"] == "technical"
        assert body["is_skeleton"] == 0

        stored = _fetch_outline_node_row(body["id"])
        assert stored == {
            "title": "人工新增施工方案",
            "parent_id": parent_id,
            "depth": 1,
            "sort_order": 1,
            "node_type": "free_writing",
            "target_word_count": 1200,
            "volume_type": "technical",
            "is_skeleton": 0,
            "status": "user_edited",
        }

    def test_create_outline_node_can_insert_as_first_child(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, _ = _create_bid_writing_project_with_document(client, auth_headers)
        parent_id = _seed_outline_create_parent(project_id)

        resp = client.post(
            f"/api/writing/projects/{project_id}/outline",
            headers=auth_headers,
            json={
                "parent_id": parent_id,
                "insert_position": "first",
                "title": "紧邻卷后新增章节",
                "node_type": "free_writing",
                "target_word_count": 800,
            },
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["parent_id"] == parent_id
        assert body["sort_order"] == 0
        child_rows = _fetch_outline_child_rows(parent_id)
        assert [(row["title"], row["sort_order"]) for row in child_rows] == [
            ("紧邻卷后新增章节", 0),
            ("既有子节点", 1),
        ]

    def test_create_outline_node_can_insert_after_sibling_anchor(
        self,
        client: TestClient,
        auth_headers,
    ):
        project_id, _ = _create_bid_writing_project_with_document(client, auth_headers)
        parent_id = _seed_outline_create_parent(project_id)
        _seed_outline_child(project_id, parent_id, "后续同级节点", 1)
        first_child = _fetch_outline_child_rows(parent_id)[0]

        resp = client.post(
            f"/api/writing/projects/{project_id}/outline",
            headers=auth_headers,
            json={
                "parent_id": parent_id,
                "insert_after_id": first_child["id"],
                "title": "紧邻投标函下方章节",
                "node_type": "data_fill",
                "target_word_count": 600,
            },
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["parent_id"] == parent_id
        assert body["sort_order"] == 1
        child_rows = _fetch_outline_child_rows(parent_id)
        assert [(row["title"], row["sort_order"]) for row in child_rows] == [
            ("既有子节点", 0),
            ("紧邻投标函下方章节", 1),
            ("后续同级节点", 2),
        ]
