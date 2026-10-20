"""LLM Center -- 管理动作审计日志防御测试

验证所有写操作均产生审计记录，覆盖步骤 1 补齐的 audit_log 接入点。
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings

_API_PREFIX = "/api/admin/llm-center"


def _sync_url() -> str:
    return settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


def _count_audit_logs(action: str, detail_contains: str = "") -> int:
    engine = create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            sql = """
                SELECT COUNT(*) FROM audit_logs
                WHERE category = 'llm_center' AND action = :action
            """
            params: dict = {"action": action}
            if detail_contains:
                sql += " AND detail LIKE :pattern"
                params["pattern"] = f"%{detail_contains}%"
            return conn.execute(text(sql), params).scalar() or 0
    finally:
        engine.dispose()


def _pick_editable_node(client: TestClient, admin_headers: dict) -> str | None:
    flows_resp = client.get(f"{_API_PREFIX}/flows", headers=admin_headers)
    if flows_resp.status_code != 200:
        return None
    for f in flows_resp.json():
        topo = client.get(f"{_API_PREFIX}/flows/{f['flow_key']}", headers=admin_headers)
        if topo.status_code != 200:
            continue
        for stage in topo.json().get("stages", []):
            for node in stage.get("nodes", []):
                nk = node["node_key"]
                detail = client.get(f"{_API_PREFIX}/nodes/{nk}", headers=admin_headers)
                if detail.status_code != 200:
                    continue
                d = detail.json()
                if d.get("editable_level") != "readonly" and d.get("all_versions"):
                    return nk
    return None


def test_save_draft_creates_audit(client: TestClient, admin_headers: dict):
    """保存草稿后 audit_logs 应有 save_draft 记录"""
    nk = _pick_editable_node(client, admin_headers)
    if nk is None:
        import pytest
        pytest.skip("无可编辑节点")

    before = _count_audit_logs("save_draft", nk)
    resp = client.post(
        f"{_API_PREFIX}/nodes/{nk}/draft",
        json={"template_role": "user", "content": "audit test draft {placeholder}"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, f"创建草稿失败: {resp.text}"
    try:
        after = _count_audit_logs("save_draft", nk)
        assert after > before, "保存草稿后应新增 save_draft 审计记录"
    finally:
        client.delete(f"{_API_PREFIX}/nodes/{nk}/draft", headers=admin_headers)


def test_validate_creates_audit(client: TestClient, admin_headers: dict):
    """校验草稿后 audit_logs 应有 validate_draft 记录"""
    nk = _pick_editable_node(client, admin_headers)
    if nk is None:
        import pytest
        pytest.skip("无可编辑节点")

    client.post(
        f"{_API_PREFIX}/nodes/{nk}/draft",
        json={"template_role": "user", "content": "validate audit test"},
        headers=admin_headers,
    )
    try:
        before = _count_audit_logs("validate_draft", nk)
        resp = client.post(
            f"{_API_PREFIX}/nodes/{nk}/validate",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        after = _count_audit_logs("validate_draft", nk)
        assert after > before, "校验后应新增 validate_draft 审计记录"
    finally:
        client.delete(f"{_API_PREFIX}/nodes/{nk}/draft", headers=admin_headers)


def test_discard_draft_creates_audit(client: TestClient, admin_headers: dict):
    """丢弃草稿后 audit_logs 应有 discard_draft 记录"""
    nk = _pick_editable_node(client, admin_headers)
    if nk is None:
        import pytest
        pytest.skip("无可编辑节点")

    client.post(
        f"{_API_PREFIX}/nodes/{nk}/draft",
        json={"template_role": "user", "content": "discard audit test"},
        headers=admin_headers,
    )
    before = _count_audit_logs("discard_draft", nk)
    resp = client.delete(f"{_API_PREFIX}/nodes/{nk}/draft", headers=admin_headers)
    assert resp.status_code == 200
    after = _count_audit_logs("discard_draft", nk)
    assert after > before, "丢弃草稿后应新增 discard_draft 审计记录"


def test_resync_creates_audit(client: TestClient, admin_headers: dict):
    """手动 resync 后 audit_logs 应有 resync_registry 记录"""
    before = _count_audit_logs("resync_registry")
    resp = client.post(f"{_API_PREFIX}/registry/resync", headers=admin_headers)
    assert resp.status_code == 200, f"resync 失败: {resp.text}"
    after = _count_audit_logs("resync_registry")
    assert after > before, "resync 后应新增 resync_registry 审计记录"
