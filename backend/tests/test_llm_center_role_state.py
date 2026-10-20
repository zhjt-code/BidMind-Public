"""LLM Center -- 校验/发布按 template_role 隔离的防御测试

覆盖 两个核心场景:
 1. 校验失败时, 草稿保持 draft 状态, 不写 validated
 2. 仅 user 草稿通过校验并发布时, system active 版本不受影响

占位符基线校验:
 3. 复制 active 内容为草稿时, 即使 param_defs 不完整也应通过校验
 4. 新增真正未定义的占位符时, 校验仍能检出
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings

_API_PREFIX = "/api/admin/llm-center"


def _sync_url() -> str:
    return settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


def _query_versions(node_key: str) -> list[dict]:
    engine = create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT v.id, v.template_role, v.version, v.status,
                       v.is_active, v.validated_at, v.content
                FROM llm_template_versions v
                JOIN llm_nodes n ON n.id = v.node_id
                WHERE n.node_key = :nk
                ORDER BY v.template_role, v.version DESC
            """), {"nk": node_key}).mappings().all()
            return [dict(r) for r in rows]
    finally:
        engine.dispose()


def _insert_validated_version(node_key: str, role: str, content: str) -> dict:
    """直接构造 validated 版本, 用于覆盖历史脏数据路径。"""
    engine = create_engine(_sync_url())
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT n.id AS node_id, COALESCE(MAX(v.version), 0) AS max_version
                FROM llm_nodes n
                LEFT JOIN llm_template_versions v ON v.node_id = n.id
                WHERE n.node_key = :nk
                GROUP BY n.id
            """), {"nk": node_key}).mappings().one()
            version = int(row["max_version"]) + 1
            version_id = conn.execute(text("""
                INSERT INTO llm_template_versions (
                    node_id, template_role, version, content, is_active,
                    source, status, change_note, created_by,
                    validated_at, created_at, updated_at
                )
                VALUES (
                    :node_id, :role, :version, :content, FALSE,
                    'db', 'validated', 'test validated candidate', 'test',
                    NOW(), NOW(), NOW()
                )
                RETURNING id
            """), {
                "node_id": row["node_id"],
                "role": role,
                "version": version,
                "content": content,
            }).scalar_one()
            return {"id": version_id, "version": version}
    finally:
        engine.dispose()


def _restore_single_active_role(
    node_key: str,
    role: str,
    active_id: int,
    archived_ids: list[int],
) -> None:
    """恢复测试前的单 role active 版本, 避免测试污染后续用例。"""
    engine = create_engine(_sync_url())
    try:
        with engine.begin() as conn:
            node_id = conn.execute(text("""
                SELECT id FROM llm_nodes WHERE node_key = :nk
            """), {"nk": node_key}).scalar_one()
            conn.execute(text("""
                UPDATE llm_template_versions
                SET is_active = FALSE,
                    status = CASE WHEN status = 'published' THEN 'archived' ELSE status END,
                    updated_at = NOW()
                WHERE node_id = :node_id AND template_role = :role
            """), {"node_id": node_id, "role": role})
            for version_id in archived_ids:
                conn.execute(text("""
                    UPDATE llm_template_versions
                    SET is_active = FALSE, status = 'archived', updated_at = NOW()
                    WHERE id = :id
                """), {"id": version_id})
            conn.execute(text("""
                UPDATE llm_template_versions
                SET is_active = TRUE, status = 'published', updated_at = NOW()
                WHERE id = :id
            """), {"id": active_id})
    finally:
        engine.dispose()


def _delete_versions(version_ids: list[int]) -> None:
    """删除测试直造版本, 避免 archived 垃圾版本长期累积。

 先删引用这些版本的 llm_publish_records (含 previous_version_id 外键),
 否则发布/回滚产生的记录会阻塞版本删除 (测试清理路径)。
 """
    if not version_ids:
        return
    engine = create_engine(_sync_url())
    try:
        with engine.begin() as conn:
            for version_id in version_ids:
                conn.execute(text("""
                    DELETE FROM llm_publish_records
                    WHERE template_version_id = :id OR previous_version_id = :id
                """), {"id": version_id})
                conn.execute(text("""
                    DELETE FROM llm_template_versions WHERE id = :id
                """), {"id": version_id})
    finally:
        engine.dispose()


def _pick_testable_node(
    client: TestClient,
    admin_headers: dict,
    *,
    for_publish: bool = False,
) -> str | None:
    """找一个同时拥有 system + user active 版本的节点。

    for_publish=True 时, 额外验证 user 草稿能通过校验 (创建后立即丢弃)。
    """
    flows_resp = client.get(f"{_API_PREFIX}/flows", headers=admin_headers)
    if flows_resp.status_code != 200:
        return None

    flow_keys = [f["flow_key"] for f in flows_resp.json()]
    for fk in flow_keys:
        topo_resp = client.get(f"{_API_PREFIX}/flows/{fk}", headers=admin_headers)
        if topo_resp.status_code != 200:
            continue
        topo = topo_resp.json()
        for stage in topo.get("stages", []):
            for node in stage.get("nodes", []):
                nk = node["node_key"]
                detail_resp = client.get(
                    f"{_API_PREFIX}/nodes/{nk}", headers=admin_headers,
                )
                if detail_resp.status_code != 200:
                    continue
                detail = detail_resp.json()
                active_versions = [
                    v for v in detail.get("all_versions", []) if v["is_active"]
                ]
                active_roles = {v["template_role"] for v in active_versions}
                if "system" not in active_roles or "user" not in active_roles:
                    continue

                if not for_publish:
                    return nk

                user_v = next(
                    v for v in active_versions if v["template_role"] == "user"
                )
                dr = client.post(
                    f"{_API_PREFIX}/nodes/{nk}/draft",
                    json={
                        "template_role": "user",
                        "content": user_v["content"].rstrip() + "\n<!-- probe -->",
                    },
                    headers=admin_headers,
                )
                if dr.status_code != 200:
                    continue
                vr = client.post(
                    f"{_API_PREFIX}/nodes/{nk}/validate",
                    headers=admin_headers,
                )
                cleanup = client.delete(
                    f"{_API_PREFIX}/nodes/{nk}/draft", headers=admin_headers,
                )
                assert cleanup.status_code == 200
                if vr.status_code == 200 and vr.json().get("passed"):
                    return nk
    return None


# ────────────────────────────────────────────────
# Test 1: 校验失败时草稿保持 draft
# ────────────────────────────────────────────────

def test_validate_failure_keeps_draft(
    client: TestClient, admin_headers: dict
):
    """校验未通过时, 草稿状态不得变为 validated"""
    node_key = _pick_testable_node(client, admin_headers)
    if node_key is None:
        import pytest
        pytest.skip("无同时拥有 system+user active 版本的节点, 跳过")

    draft_content = "测试草稿 -- 含未定义占位符: {undefined_placeholder_xyz}"
    resp = client.post(
        f"{_API_PREFIX}/nodes/{node_key}/draft",
        json={"template_role": "user", "content": draft_content},
        headers=admin_headers,
    )
    assert resp.status_code == 200, f"创建草稿失败: {resp.text}"
    draft_id = resp.json()["id"]

    try:
        val_resp = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/validate",
            headers=admin_headers,
        )
        assert val_resp.status_code == 200
        val_data = val_resp.json()
        assert val_data["passed"] is False, "含未定义占位符应校验失败"

        versions = _query_versions(node_key)
        draft_row = next((v for v in versions if v["id"] == draft_id), None)
        assert draft_row is not None, "草稿版本应仍存在"
        assert draft_row["status"] == "draft", (
            f"校验失败时状态应保持 draft, 实际为 {draft_row['status']}"
        )
        assert draft_row["validated_at"] is None, (
            "校验失败时 validated_at 应为空"
        )
    finally:
        client.delete(
            f"{_API_PREFIX}/nodes/{node_key}/draft",
            headers=admin_headers,
        )


# ────────────────────────────────────────────────
# Test 2: 仅发布 user 时, system active 不受影响
# ────────────────────────────────────────────────

def test_publish_user_preserves_system_active(
    client: TestClient, admin_headers: dict
):
    """发布 user validated 草稿时, system active 版本应保持不变"""
    node_key = _pick_testable_node(client, admin_headers, for_publish=True)
    if node_key is None:
        import pytest
        pytest.skip("无满足条件(双 role active 且 user 可通过校验)的节点, 跳过")

    versions_before = _query_versions(node_key)
    system_active_before = [
        v for v in versions_before
        if v["template_role"] == "system" and v["is_active"]
    ]
    assert len(system_active_before) > 0, "测试前提: 需要 system active 版本"
    sys_active_id = system_active_before[0]["id"]

    user_active_before = [
        v for v in versions_before
        if v["template_role"] == "user" and v["is_active"]
    ]
    assert len(user_active_before) > 0, "测试前提: 需要 user active 版本"
    user_active_content = user_active_before[0]["content"]

    # 保持占位符不变，仅追加不含占位符的空注释行
    new_content = user_active_content.rstrip() + "\n<!-- test-marker -->"
    resp = client.post(
        f"{_API_PREFIX}/nodes/{node_key}/draft",
        json={
            "template_role": "user",
            "content": new_content,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    draft_id = resp.json()["id"]

    published = False
    try:
        val_resp = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/validate",
            headers=admin_headers,
        )
        assert val_resp.status_code == 200
        assert val_resp.json()["passed"], (
            f"校验应通过(已预验证): {val_resp.json()}"
        )

        pub_resp = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/publish",
            json={},
            headers=admin_headers,
        )
        assert pub_resp.status_code == 200, f"发布失败: {pub_resp.text}"
        published = True

        versions_after = _query_versions(node_key)

        sys_active_after = [
            v for v in versions_after
            if v["template_role"] == "system" and v["is_active"]
        ]
        assert len(sys_active_after) == 1, (
            "发布 user 后, system 应仍有 active 版本"
        )
        assert sys_active_after[0]["id"] == sys_active_id, (
            "发布 user 后, system active 版本 ID 应不变"
        )

        user_active_after = [
            v for v in versions_after
            if v["template_role"] == "user" and v["is_active"]
        ]
        assert len(user_active_after) == 1, "发布后 user 应只有一个 active 版本"
        assert user_active_after[0]["id"] == draft_id, (
            "新发布的 user 版本应为刚才的草稿"
        )

    finally:
        if published:
            client.post(
                f"{_API_PREFIX}/nodes/{node_key}/rollback",
                headers=admin_headers,
            )
        else:
            client.delete(
                f"{_API_PREFIX}/nodes/{node_key}/draft",
                headers=admin_headers,
            )


# ────────────────────────────────────────────────
# Test 3: 同 role 多 validated 发布时只激活最新版本
# ────────────────────────────────────────────────

def test_publish_only_latest_validated_per_role(
    client: TestClient, admin_headers: dict
):
    """同一 role 残留多个 validated 时, 发布只能激活最新一个。"""
    node_key = _pick_testable_node(client, admin_headers)
    if node_key is None:
        import pytest
        pytest.skip("无同时拥有 system+user active 版本的节点, 跳过")

    versions_before = _query_versions(node_key)
    user_active_before = [
        v for v in versions_before
        if v["template_role"] == "user" and v["is_active"]
    ]
    assert len(user_active_before) == 1, "测试前提: user 应只有一个 active 版本"
    original_user_id = user_active_before[0]["id"]
    base_content = user_active_before[0]["content"]

    older = _insert_validated_version(
        node_key, "user", base_content.rstrip() + "\n<!-- stale validated -->",
    )
    newer = _insert_validated_version(
        node_key, "user", base_content.rstrip() + "\n<!-- latest validated -->",
    )

    try:
        pub_resp = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/publish",
            json={},
            headers=admin_headers,
        )
        assert pub_resp.status_code == 200, f"发布失败: {pub_resp.text}"

        versions_after = _query_versions(node_key)
        user_active_after = [
            v for v in versions_after
            if v["template_role"] == "user" and v["is_active"]
        ]
        assert len(user_active_after) == 1, "发布后 user 只能有一个 active 版本"
        assert user_active_after[0]["id"] == newer["id"], (
            "同 role 多个 validated 时应只激活最高 version/id 的候选"
        )

        older_row = next(v for v in versions_after if v["id"] == older["id"])
        assert older_row["status"] == "archived"
        assert older_row["is_active"] is False
    finally:
        _restore_single_active_role(
            node_key,
            "user",
            original_user_id,
            [older["id"], newer["id"]],
        )


# ────────────────────────────────────────────────
# Test 4: 保存新草稿时归档同 role 旧 validated
# ────────────────────────────────────────────────

def test_new_draft_archives_same_role_validated(
    client: TestClient, admin_headers: dict
):
    """同 role 已校验未发布时, 再次保存草稿应归档旧 validated。"""
    node_key = _pick_testable_node(client, admin_headers)
    if node_key is None:
        import pytest
        pytest.skip("无同时拥有 system+user active 版本的节点, 跳过")

    versions_before = _query_versions(node_key)
    user_active = next(
        (
            v for v in versions_before
            if v["template_role"] == "user" and v["is_active"]
        ),
        None,
    )
    assert user_active is not None
    created_ids: list[int] = []

    try:
        first = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/draft",
            json={
                "template_role": "user",
                "content": user_active["content"].rstrip() + "\n<!-- first validated -->",
            },
            headers=admin_headers,
        )
        assert first.status_code == 200
        first_id = first.json()["id"]
        created_ids.append(first_id)

        val_resp = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/validate",
            headers=admin_headers,
        )
        assert val_resp.status_code == 200
        assert val_resp.json()["passed"]

        first_validated = next(
            v for v in _query_versions(node_key) if v["id"] == first_id
        )
        assert first_validated["status"] == "validated"
        assert first_validated["is_active"] is False

        second = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/draft",
            json={
                "template_role": "user",
                "content": user_active["content"].rstrip() + "\n<!-- second draft -->",
            },
            headers=admin_headers,
        )
        assert second.status_code == 200
        second_id = second.json()["id"]
        created_ids.append(second_id)

        versions_after = _query_versions(node_key)
        first_archived = next(v for v in versions_after if v["id"] == first_id)
        second_draft = next(v for v in versions_after if v["id"] == second_id)
        assert first_archived["status"] == "archived"
        assert first_archived["is_active"] is False
        assert second_draft["status"] == "draft"
        assert second_draft["is_active"] is False
    finally:
        client.delete(
            f"{_API_PREFIX}/nodes/{node_key}/draft",
            headers=admin_headers,
        )
        _delete_versions(created_ids)


# ────────────────────────────────────────────────
# Test 5: active 内容作为草稿时校验应通过 (基线逻辑)
# ────────────────────────────────────────────────

def test_active_content_draft_passes_validation(
    client: TestClient, admin_headers: dict
):
    """将 active 版本内容原封不动保存为草稿, 校验应通过,
    即使 param_defs 不覆盖全部占位符 (基线豁免)。"""
    node_key = _pick_testable_node(client, admin_headers)
    if node_key is None:
        import pytest
        pytest.skip("无同时拥有 system+user active 版本的节点, 跳过")

    detail_resp = client.get(
        f"{_API_PREFIX}/nodes/{node_key}", headers=admin_headers,
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    user_active = next(
        (v for v in detail["all_versions"]
         if v["is_active"] and v["template_role"] == "user"),
        None,
    )
    assert user_active is not None

    resp = client.post(
        f"{_API_PREFIX}/nodes/{node_key}/draft",
        json={
            "template_role": "user",
            "content": user_active["content"],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200

    try:
        val_resp = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/validate",
            headers=admin_headers,
        )
        assert val_resp.status_code == 200
        val_data = val_resp.json()
        failed = [c for c in val_data["checks"] if not c["passed"]]
        assert val_data["passed"], (
            f"active 内容的草稿校验应通过(基线豁免), 失败项: {failed}"
        )
    finally:
        client.delete(
            f"{_API_PREFIX}/nodes/{node_key}/draft",
            headers=admin_headers,
        )


# ────────────────────────────────────────────────
# Test 6: 新增未定义占位符仍被检出
# ────────────────────────────────────────────────

def test_new_undefined_placeholder_detected(
    client: TestClient, admin_headers: dict
):
    """在 active 内容基础上新增未定义的占位符, 校验应失败并报告该占位符。"""
    node_key = _pick_testable_node(client, admin_headers)
    if node_key is None:
        import pytest
        pytest.skip("无同时拥有 system+user active 版本的节点, 跳过")

    detail_resp = client.get(
        f"{_API_PREFIX}/nodes/{node_key}", headers=admin_headers,
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    user_active = next(
        (v for v in detail["all_versions"]
         if v["is_active"] and v["template_role"] == "user"),
        None,
    )
    assert user_active is not None

    injected = "totally_fake_param_xyz"
    draft_content = user_active["content"] + f"\n{{{injected}}}"
    resp = client.post(
        f"{_API_PREFIX}/nodes/{node_key}/draft",
        json={"template_role": "user", "content": draft_content},
        headers=admin_headers,
    )
    assert resp.status_code == 200

    try:
        val_resp = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/validate",
            headers=admin_headers,
        )
        assert val_resp.status_code == 200
        val_data = val_resp.json()
        assert val_data["passed"] is False, (
            "含新增未定义占位符的草稿校验应失败"
        )
        ph_checks = [
            c for c in val_data["checks"]
            if c["check_type"] == "placeholder_match" and not c["passed"]
        ]
        assert len(ph_checks) > 0, "应有 placeholder_match 失败项"
        all_undefined = []
        for c in ph_checks:
            all_undefined.extend(c.get("details", {}).get("undefined", []))
        assert injected in all_undefined, (
            f"新增占位符 '{injected}' 应在未定义列表中, 实际: {all_undefined}"
        )
    finally:
        client.delete(
            f"{_API_PREFIX}/nodes/{node_key}/draft",
            headers=admin_headers,
        )


# ────────────────────────────────────────────────
# Test 7: 陈旧 validated 草稿低于激活版本时归档不降级
# ────────────────────────────────────────────────

def _promote_active_out_of_band(version_id: int, node_key: str, role: str) -> None:
    """模拟脚本外部发布: 直接把指定版本设为该 role 唯一 active。"""
    engine = create_engine(_sync_url())
    try:
        with engine.begin() as conn:
            node_id = conn.execute(text(
                "SELECT id FROM llm_nodes WHERE node_key = :nk"
            ), {"nk": node_key}).scalar_one()
            conn.execute(text("""
                UPDATE llm_template_versions
                SET is_active = FALSE,
                    status = CASE WHEN status = 'published' THEN 'archived' ELSE status END,
                    updated_at = NOW()
                WHERE node_id = :node_id AND template_role = :role AND is_active = TRUE
            """), {"node_id": node_id, "role": role})
            conn.execute(text("""
                UPDATE llm_template_versions
                SET is_active = TRUE, status = 'published',
                    published_at = NOW(), updated_at = NOW()
                WHERE id = :id
            """), {"id": version_id})
    finally:
        engine.dispose()


def test_publish_stale_validated_below_active_archived_not_downgraded(
    client: TestClient, admin_headers: dict
):
    """ 回归: 遗留 validated 草稿版本低于当前激活版本时,
 发布必须归档该草稿而不是激活它(内容降级), 更不允许撞
 uq_llm_tv_active_node_role 报 500。

 复现形态: 脚本外部发布高版本(v8)后, seed 中间态草稿(v7)残留。
 """
    node_key = _pick_testable_node(client, admin_headers)
    if node_key is None:
        import pytest
        pytest.skip("无同时拥有 system+user active 版本的节点, 跳过")

    versions_before = _query_versions(node_key)
    user_active_before = next(
        v for v in versions_before
        if v["template_role"] == "user" and v["is_active"]
    )
    original_user_id = user_active_before["id"]
    base_content = user_active_before["content"]

    # stale: 低版本 validated 草稿; newer: 高版本, 模拟外部脚本直接发布
    stale = _insert_validated_version(
        node_key, "user", base_content.rstrip() + "\n<!-- stale mid-state -->",
    )
    newer = _insert_validated_version(
        node_key, "user", base_content.rstrip() + "\n<!-- out-of-band published -->",
    )
    _promote_active_out_of_band(newer["id"], node_key, "user")

    try:
        pub_resp = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/publish",
            json={},
            headers=admin_headers,
        )
        # 旧实现: UniqueViolationError -> 500; 新实现: 归档陈旧草稿,
        # 无可发布目标时 ServiceError(400)
        assert pub_resp.status_code != 500, f"发布撞唯一索引: {pub_resp.text}"

        versions_after = _query_versions(node_key)
        stale_row = next(v for v in versions_after if v["id"] == stale["id"])
        newer_row = next(v for v in versions_after if v["id"] == newer["id"])
        assert stale_row["status"] == "archived", "陈旧草稿应被归档"
        assert stale_row["is_active"] is False
        assert newer_row["is_active"] is True, "高版本激活内容不得被降级替换"

        user_active_after = [
            v for v in versions_after
            if v["template_role"] == "user" and v["is_active"]
        ]
        assert len(user_active_after) == 1, "发布后 user 只能有一个 active 版本"
    finally:
        _restore_single_active_role(
            node_key,
            "user",
            original_user_id,
            [stale["id"], newer["id"]],
        )
        _delete_versions([stale["id"], newer["id"]])


# ────────────────────────────────────────────────
# Test 8: 回滚激活低主键版本不得撞唯一索引
# ────────────────────────────────────────────────

def test_rollback_activates_older_version_without_unique_violation(
    client: TestClient, admin_headers: dict
):
    """ 回归: 回滚目标主键必然小于当前激活行,
 取消激活与激活若在同一 flush 内按主键顺序刷写会瞬时违反
 uq_llm_tv_active_node_role; 修复后回滚必须成功。
 """
    node_key = _pick_testable_node(client, admin_headers)
    if node_key is None:
        import pytest
        pytest.skip("无同时拥有 system+user active 版本的节点, 跳过")

    versions_before = _query_versions(node_key)
    original_active_by_role = {
        v["template_role"]: v["id"]
        for v in versions_before if v["is_active"]
    }
    user_active_before = next(
        v for v in versions_before
        if v["template_role"] == "user" and v["is_active"]
    )

    # 构造高主键激活版本, 使原 active 成为回滚候选(低主键)
    newer = _insert_validated_version(
        node_key, "user",
        user_active_before["content"].rstrip() + "\n<!-- rollback source -->",
    )
    _promote_active_out_of_band(newer["id"], node_key, "user")

    try:
        rb_resp = client.post(
            f"{_API_PREFIX}/nodes/{node_key}/rollback",
            headers=admin_headers,
        )
        assert rb_resp.status_code == 200, (
            f"回滚失败(疑似唯一索引瞬时冲突): {rb_resp.text}"
        )

        versions_after = _query_versions(node_key)
        for role in {v["template_role"] for v in versions_after}:
            active_rows = [
                v for v in versions_after
                if v["template_role"] == role and v["is_active"]
            ]
            assert len(active_rows) <= 1, f"role={role} 出现多个 active 版本"
    finally:
        for role, active_id in original_active_by_role.items():
            _restore_single_active_role(node_key, role, active_id, [newer["id"]])
        _delete_versions([newer["id"]])
