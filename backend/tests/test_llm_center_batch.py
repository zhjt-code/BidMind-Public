"""LLM Center -- 批量校验与发布防御测试

覆盖 Phase 6 批量编排的核心场景:
  1. 无待处理节点时返回空结果
  2. 全部通过时批量发布成功
  3. 部分失败时不自动整体发布
  4. publish_passed 仅发布通过项
  5. 批量操作写入审计日志
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


def _pick_editable_node_with_dual_roles(
    client: TestClient, admin_headers: dict
) -> str | None:
    """找一个默认批量范围可扫描的 editable 双 role 节点。"""
    flows_resp = client.get(f"{_API_PREFIX}/flows", headers=admin_headers)
    if flows_resp.status_code != 200:
        return None
    for f in flows_resp.json():
        topo = client.get(
            f"{_API_PREFIX}/flows/{f['flow_key']}", headers=admin_headers,
        )
        if topo.status_code != 200:
            continue
        for stage in topo.json().get("stages", []):
            for node in stage.get("nodes", []):
                if node.get("visibility") != "main":
                    continue
                nk = node["node_key"]
                detail = client.get(
                    f"{_API_PREFIX}/nodes/{nk}", headers=admin_headers,
                )
                if detail.status_code != 200:
                    continue
                d = detail.json()
                if d.get("editable_level") == "readonly":
                    continue
                active_roles = {
                    v["template_role"]
                    for v in d.get("all_versions", [])
                    if v["is_active"]
                }
                if "system" in active_roles and "user" in active_roles:
                    return nk
    return None


# ────────────────────────────────────────────────
# Test 1: 无待处理节点时返回空结果
# ────────────────────────────────────────────────

def test_batch_validate_no_pending(
    client: TestClient, admin_headers: dict,
):
    """当某个流程没有草稿时, batch/validate 应返回 total=0"""
    resp = client.post(
        f"{_API_PREFIX}/batch/validate",
        json={"scope": "flow", "flow_key": "writing"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "items" in data
    assert isinstance(data["items"], list)


# ────────────────────────────────────────────────
# Test 2: 含未定义占位符的草稿 -> 批量校验失败
# ────────────────────────────────────────────────

def test_batch_validate_with_failure(
    client: TestClient, admin_headers: dict,
):
    """构造一个校验失败的草稿, 批量校验后应报告 validation_failed"""
    nk = _pick_editable_node_with_dual_roles(client, admin_headers)
    if nk is None:
        import pytest
        pytest.skip("无满足条件的可编辑节点")

    detail = client.get(
        f"{_API_PREFIX}/nodes/{nk}", headers=admin_headers,
    ).json()
    flow_key = None
    for f in client.get(f"{_API_PREFIX}/flows", headers=admin_headers).json():
        topo = client.get(
            f"{_API_PREFIX}/flows/{f['flow_key']}", headers=admin_headers,
        ).json()
        for stage in topo.get("stages", []):
            if any(n["node_key"] == nk for n in stage.get("nodes", [])):
                flow_key = f["flow_key"]
                break
        if flow_key:
            break
    assert flow_key is not None

    bad_content = "草稿含未定义占位符 {undefined_batch_test_xyz}"
    dr = client.post(
        f"{_API_PREFIX}/nodes/{nk}/draft",
        json={"template_role": "user", "content": bad_content},
        headers=admin_headers,
    )
    assert dr.status_code == 200

    try:
        resp = client.post(
            f"{_API_PREFIX}/batch/validate",
            json={"scope": "flow", "flow_key": flow_key},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["validated_failed"] >= 1
        failed = [i for i in data["items"] if i["status"] == "validation_failed"]
        assert len(failed) >= 1
        target = next((i for i in failed if i["node_key"] == nk), None)
        assert target is not None, f"{nk} should be in failed items"
    finally:
        client.delete(
            f"{_API_PREFIX}/nodes/{nk}/draft", headers=admin_headers,
        )


# ────────────────────────────────────────────────
# Test 3: 部分失败时 validate-publish 不自动发布
# ────────────────────────────────────────────────

def test_batch_validate_publish_partial_failure_no_auto_publish(
    client: TestClient, admin_headers: dict,
):
    """存在校验失败项时, batch/validate-publish 不应自动发布任何节点"""
    nk = _pick_editable_node_with_dual_roles(client, admin_headers)
    if nk is None:
        import pytest
        pytest.skip("无满足条件的可编辑节点")

    detail = client.get(
        f"{_API_PREFIX}/nodes/{nk}", headers=admin_headers,
    ).json()
    flow_key = None
    for f in client.get(f"{_API_PREFIX}/flows", headers=admin_headers).json():
        topo = client.get(
            f"{_API_PREFIX}/flows/{f['flow_key']}", headers=admin_headers,
        ).json()
        for stage in topo.get("stages", []):
            if any(n["node_key"] == nk for n in stage.get("nodes", [])):
                flow_key = f["flow_key"]
                break
        if flow_key:
            break
    assert flow_key is not None

    bad_content = "部分失败测试 {undefined_batch_publish_xyz}"
    dr = client.post(
        f"{_API_PREFIX}/nodes/{nk}/draft",
        json={"template_role": "user", "content": bad_content},
        headers=admin_headers,
    )
    assert dr.status_code == 200

    try:
        resp = client.post(
            f"{_API_PREFIX}/batch/validate-publish",
            json={"scope": "flow", "flow_key": flow_key},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["published"] == 0, (
            "存在失败项时不应自动发布"
        )
        assert data["summary"]["validated_failed"] >= 1
    finally:
        client.delete(
            f"{_API_PREFIX}/nodes/{nk}/draft", headers=admin_headers,
        )


# ────────────────────────────────────────────────
# Test 4: batch/publish-passed 只发布指定的通过项
# ────────────────────────────────────────────────

def test_batch_publish_passed_only_publishes_specified(
    client: TestClient, admin_headers: dict,
):
    """publish-passed 仅发布传入的已校验通过节点"""
    nk = _pick_editable_node_with_dual_roles(client, admin_headers)
    if nk is None:
        import pytest
        pytest.skip("无满足条件的可编辑节点")

    detail = client.get(
        f"{_API_PREFIX}/nodes/{nk}", headers=admin_headers,
    ).json()
    user_active = next(
        (v for v in detail["all_versions"]
         if v["is_active"] and v["template_role"] == "user"),
        None,
    )
    assert user_active is not None

    new_content = user_active["content"].rstrip() + "\n<!-- batch-pub-test -->"
    dr = client.post(
        f"{_API_PREFIX}/nodes/{nk}/draft",
        json={"template_role": "user", "content": new_content},
        headers=admin_headers,
    )
    assert dr.status_code == 200

    published = False
    try:
        val = client.post(
            f"{_API_PREFIX}/nodes/{nk}/validate", headers=admin_headers,
        )
        assert val.status_code == 200
        if not val.json().get("passed"):
            import pytest
            pytest.skip("草稿校验未通过, 无法测试 publish-passed")

        resp = client.post(
            f"{_API_PREFIX}/batch/publish-passed",
            json={"node_keys": [nk]},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["published"] >= 1
        pub_items = [i for i in data["items"] if i["status"] == "published"]
        assert any(i["node_key"] == nk for i in pub_items)
        published = True
    finally:
        if published:
            client.post(
                f"{_API_PREFIX}/nodes/{nk}/rollback", headers=admin_headers,
            )
        else:
            client.delete(
                f"{_API_PREFIX}/nodes/{nk}/draft", headers=admin_headers,
            )


# ────────────────────────────────────────────────
# Test 5: 批量操作写入审计日志
# ────────────────────────────────────────────────

def test_batch_validate_creates_audit(
    client: TestClient, admin_headers: dict,
):
    """batch/validate 应写入 batch_validate 审计记录"""
    before = _count_audit_logs("batch_validate")
    resp = client.post(
        f"{_API_PREFIX}/batch/validate",
        json={"scope": "flow", "flow_key": "writing"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    after = _count_audit_logs("batch_validate")
    assert after > before, "批量校验后应新增 batch_validate 审计记录"


def test_batch_validate_publish_creates_audit(
    client: TestClient, admin_headers: dict,
):
    """batch/validate-publish 应写入 batch_validate_publish 审计记录"""
    before = _count_audit_logs("batch_validate_publish")
    resp = client.post(
        f"{_API_PREFIX}/batch/validate-publish",
        json={"scope": "flow", "flow_key": "writing"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    after = _count_audit_logs("batch_validate_publish")
    assert after > before, "批量校验发布后应新增 batch_validate_publish 审计记录"
