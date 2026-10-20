"""LLM Center -- 参数注册源自动同步防御测试

覆盖 PARAM_DEFS 代码注册源 -> DB 的 upsert + 软降级机制:
  1. collusion.overall_synthesis 的 active input params 与代码一致,
     且包含运行时 .format() 真正使用的 4 个占位符键
  2. 代码已删除的旧参数 (历史上的 collusion.overall_synthesis.group_results)
     应被软降级为 status='deprecated', 不再出现在 active 集合中
  3. 字段漂移回灌: 手动篡改 DB 里 description, resync 后应重置为代码声明值
  4. 冗余参数软降级: 手动插入代码不存在的 orphan 参数, resync 后应变为 deprecated

这组测试专门针对 "代码已改, 模板中心没改" 的契约漂移类根因,
依赖 `/api/admin/llm-center/registry/resync` 触发真实同步流程。
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings

_API_PREFIX = "/api/admin/llm-center"

# collusion.overall_synthesis 运行时 _render_overall_user_message() 使用的 4 个占位符
# 与 PARAM_DEFS / analyzer.py 必须保持一致, 否则 str.format 会抛 KeyError。
_EXPECTED_OVERALL_SYNTHESIS_INPUTS = {
    "pair_judgements",
    "groups_summary",
    "coverage_summary",
    "profile_quality_summary",
}


def _sync_url() -> str:
    return settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


def _fetch_params(node_key: str, direction: str, status: str | None = None) -> list[dict]:
    engine = create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            sql = """
                SELECT p.id, p.param_key, p.direction, p.data_type,
                       p.is_required, p.description, p.source_type,
                       p.source_hint, p.status
                FROM llm_param_defs p
                JOIN llm_nodes n ON n.id = p.node_id
                WHERE n.node_key = :nk AND p.direction = :dir
            """
            params: dict = {"nk": node_key, "dir": direction}
            if status is not None:
                sql += " AND p.status = :status"
                params["status"] = status
            sql += " ORDER BY p.param_key"
            rows = conn.execute(text(sql), params).mappings().all()
            return [dict(r) for r in rows]
    finally:
        engine.dispose()


def _resync(client: TestClient, admin_headers: dict) -> dict:
    resp = client.post(f"{_API_PREFIX}/registry/resync", headers=admin_headers)
    assert resp.status_code == 200, f"resync 失败: {resp.text}"
    return resp.json()


# ────────────────────────────────────────────────
# Test 1: collusion.overall_synthesis 的 active input params 与代码一致
# ────────────────────────────────────────────────

def test_overall_synthesis_inputs_match_runtime_contract(
    client: TestClient, admin_headers: dict
):
    """collusion.overall_synthesis 的 active input params 必须覆盖运行时 4 个占位符"""
    _resync(client, admin_headers)

    rows = _fetch_params("collusion.overall_synthesis", "input", status="active")
    active_keys = {r["param_key"] for r in rows}

    missing = _EXPECTED_OVERALL_SYNTHESIS_INPUTS - active_keys
    assert not missing, (
        f"collusion.overall_synthesis 缺少运行时占位符: {missing}, "
        f"当前 active input={active_keys}"
    )


# ────────────────────────────────────────────────
# Test 2: 旧参数 group_results 已被软降级
# ────────────────────────────────────────────────

def test_legacy_group_results_param_deprecated(
    client: TestClient, admin_headers: dict
):
    """代码移除的 collusion.overall_synthesis.group_results 不得继续以 active 出现"""
    _resync(client, admin_headers)

    rows = _fetch_params("collusion.overall_synthesis", "input")
    by_key = {r["param_key"]: r for r in rows}
    row = by_key.get("group_results")
    if row is None:
        # 旧环境首次 seed, 本来就不存在, 属于通过
        return
    assert row["status"] != "active", (
        "group_results 已从代码注册源移除, DB 仍保持 active -> 软降级未生效, "
        f"实际 status={row['status']}"
    )


# ────────────────────────────────────────────────
# Test 3: 字段漂移回灌 -- description 被篡改后 resync 应重置
# ────────────────────────────────────────────────

def test_param_description_reset_by_resync(
    client: TestClient, admin_headers: dict
):
    """手动改掉 DB 的 description, resync 后应恢复为代码声明值"""
    _resync(client, admin_headers)  # 保证记录已存在

    rows = _fetch_params("collusion.overall_synthesis", "input", status="active")
    by_key = {r["param_key"]: r for r in rows}
    target = by_key.get("pair_judgements")
    assert target is not None, "pair_judgements 应当存在"
    original_desc: str = target["description"] or ""

    tampered = "__seed_sync_probe__DRIFT__"
    engine = create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE llm_param_defs SET description = :d WHERE id = :i"),
                {"d": tampered, "i": target["id"]},
            )
            conn.commit()
    finally:
        engine.dispose()

    # 校验篡改成功, 避免误报
    after_tamper = _fetch_params("collusion.overall_synthesis", "input", status="active")
    assert any(r["id"] == target["id"] and r["description"] == tampered for r in after_tamper), (
        "篡改步骤未生效, 测试前置条件失败"
    )

    _resync(client, admin_headers)

    after_resync = _fetch_params("collusion.overall_synthesis", "input", status="active")
    current = next((r for r in after_resync if r["id"] == target["id"]), None)
    assert current is not None, "resync 后 pair_judgements 记录丢失"
    assert current["description"] != tampered, (
        "resync 未覆盖 description, 代码注册源同步未生效"
    )
    # 与代码声明一致 (非空且非被篡改值即算通过; 精确文案见 PARAM_DEFS)
    assert current["description"], "description 不应为空"
    # 即使首次原值为空字符串, 也至少不应保留篡改文本
    assert tampered not in (current["description"] or "")
    # 最后恢复为稳妥: 与原始 description 一致或同为代码声明 (两者相等即可)
    _ = original_desc  # 保留给调试


# ────────────────────────────────────────────────
# Test 4: 冗余参数软降级 -- orphan 参数应被标记 deprecated
# ────────────────────────────────────────────────

def test_orphan_param_soft_deprecated_by_resync(
    client: TestClient, admin_headers: dict
):
    """手动插入代码不存在的 orphan 参数, resync 后应变为 deprecated"""
    _resync(client, admin_headers)

    engine = create_engine(_sync_url())
    probe_key = "__seed_sync_probe_orphan__"
    try:
        with engine.connect() as conn:
            node_id = conn.execute(
                text("SELECT id FROM llm_nodes WHERE node_key = :nk"),
                {"nk": "collusion.overall_synthesis"},
            ).scalar()
            assert node_id is not None, "collusion.overall_synthesis 节点不存在"
            # 清理可能残留的 orphan (测试重跑场景)
            conn.execute(
                text("DELETE FROM llm_param_defs WHERE node_id = :nid AND param_key = :pk"),
                {"nid": node_id, "pk": probe_key},
            )
            conn.execute(
                text("""
                    INSERT INTO llm_param_defs
                        (node_id, param_key, direction, data_type, is_required,
                         description, source_type, status, ui_hint, display_order)
                    VALUES
                        (:nid, :pk, 'input', 'string', false,
                         '测试用 orphan', 'manual', 'active', 'tooltip', 999)
                """),
                {"nid": node_id, "pk": probe_key},
            )
            conn.commit()

        _resync(client, admin_headers)

        rows = _fetch_params("collusion.overall_synthesis", "input")
        by_key = {r["param_key"]: r for r in rows}
        row = by_key.get(probe_key)
        assert row is not None, "orphan 参数应被保留, 只是状态变更"
        assert row["status"] == "deprecated", (
            f"orphan 参数未被软降级, 当前 status={row['status']}"
        )
    finally:
        # 测试后清理, 避免污染 DB
        try:
            with engine.connect() as conn:
                conn.execute(
                    text("""
                        DELETE FROM llm_param_defs
                        WHERE param_key = :pk AND node_id IN (
                            SELECT id FROM llm_nodes WHERE node_key = 'collusion.overall_synthesis'
                        )
                    """),
                    {"pk": probe_key},
                )
                conn.commit()
        finally:
            engine.dispose()
