"""广播站内信集成测试

按 docs/implementation_plan.md 防御测试要求覆盖 7 个关键场景：
1. 创建广播 → 普通用户在 banners 与列表中可见
2. 用户 A 关闭后 A 不再看到 banner，B 仍看到
3. 标已读未让 Banner 消失（已读 != 已关闭语义解耦）
4. 过期广播不出现在 banners 与列表
5. 软删除广播立即对所有用户不可见
6. 非 admin 调用 /api/admin/broadcasts 返回 403
7. dismissible=False 时不允许关闭

注意：实现选择"通知中心仅显示生效中的广播"，与 banner 一致。
理由：广播过期即失效语义；用户的历史反馈通过 messages 表保留。
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


# ==================== 辅助 fixtures ====================

@pytest.fixture()
def second_user_headers(client: TestClient) -> dict:
    """额外注册一个普通用户，用于权限隔离与多用户场景测试。"""
    tag = f"b{int(time.time() * 1000)}"
    user = {
        "username": f"u{tag[-9:]}",
        "email": f"u{tag[-9:]}@example.com",
        "password": "TestPass@123",
        "confirm_password": "TestPass@123",
    }
    reg = client.post("/api/auth/register", json=user)
    assert reg.status_code == 200, f"注册第二用户失败: {reg.text}"

    login = client.post("/api/auth/login", json={
        "username": user["username"],
        "password": user["password"],
    })
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def created_broadcast(client: TestClient, admin_headers: dict) -> Generator[int, None, None]:
    """创建一条简单 info 级广播，返回其 id；测试结束后软删除。"""
    payload = {
        "level": "info",
        "title": f"测试广播 {time.time():.0f}",
        "content": "这是测试广播内容",
    }
    resp = client.post("/api/admin/broadcasts", json=payload, headers=admin_headers)
    assert resp.status_code in (200, 201), f"创建广播失败: {resp.text}"
    bid = resp.json()["data"]["id"]
    yield bid
    # 清理：软删除
    client.delete(f"/api/admin/broadcasts/{bid}", headers=admin_headers)


# ==================== 场景 1: 创建后用户可见 ====================

class TestBroadcastVisibility:
    """场景 1: 创建广播后，普通用户在 banners 与通知列表中能看到。"""

    def test_visible_in_banners(self, client: TestClient, auth_headers: dict, created_broadcast: int):
        resp = client.get("/api/messages/banners", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        ids = [b["id"] for b in items]
        assert created_broadcast in ids, f"广播未在 banners 中: {items}"

    def test_visible_in_inbox(self, client: TestClient, auth_headers: dict, created_broadcast: int):
        resp = client.get("/api/messages?page=1&page_size=50", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        # 通知中心列表中广播条目 ref_type='broadcast'，ref_id 是真实广播 id
        broadcast_items = [m for m in items if m.get("ref_type") == "broadcast"]
        ref_ids = [m["ref_id"] for m in broadcast_items]
        assert created_broadcast in ref_ids, f"广播未在通知中心列表中: {broadcast_items}"

    def test_unread_count_includes_broadcast(self, client: TestClient, auth_headers: dict, created_broadcast: int):
        resp = client.get("/api/messages/unread_count", headers=auth_headers)
        assert resp.status_code == 200
        count = resp.json()["data"]["count"]
        # 至少包含本次新建的这条广播；可能还有其它历史消息，故用 >= 1
        assert count >= 1, "未读数应至少为 1（含新建广播）"


# ==================== 场景 2: 关闭后只对当前用户隐藏 ====================

class TestDismissPerUser:
    """场景 2: 用户 A 关闭 banner 后只影响 A，B 仍看到；并且关闭即视为已读。"""

    def test_dismiss_affects_only_self(
        self,
        client: TestClient,
        auth_headers: dict,
        second_user_headers: dict,
        created_broadcast: int,
    ):
        # A 关闭
        dismiss_resp = client.post(
            f"/api/messages/broadcasts/{created_broadcast}/dismiss",
            headers=auth_headers,
        )
        assert dismiss_resp.status_code == 200

        # A 的 banners 不再包含
        a_banners = client.get("/api/messages/banners", headers=auth_headers).json()["data"]["items"]
        a_ids = [b["id"] for b in a_banners]
        assert created_broadcast not in a_ids, "A 关闭后应从 A 的 banners 消失"

        # B 的 banners 仍包含
        b_banners = client.get("/api/messages/banners", headers=second_user_headers).json()["data"]["items"]
        b_ids = [b["id"] for b in b_banners]
        assert created_broadcast in b_ids, "B 应仍在 banners 中看到广播"

    def test_dismiss_marks_read(
        self,
        client: TestClient,
        auth_headers: dict,
        created_broadcast: int,
    ):
        before = client.get("/api/messages/unread_count", headers=auth_headers).json()["data"]["count"]
        client.post(
            f"/api/messages/broadcasts/{created_broadcast}/dismiss",
            headers=auth_headers,
        )
        after = client.get("/api/messages/unread_count", headers=auth_headers).json()["data"]["count"]
        assert after == before - 1, "关闭即视为已读，未读数应 -1"


# ==================== 场景 3: 已读 != 已关闭 ====================

class TestReadVsDismissDecoupled:
    """场景 3: 标已读不会让 Banner 消失（语义解耦）。"""

    def test_mark_read_keeps_banner_visible(
        self,
        client: TestClient,
        auth_headers: dict,
        created_broadcast: int,
    ):
        # 标已读
        read_resp = client.patch(
            f"/api/messages/broadcasts/{created_broadcast}/read",
            headers=auth_headers,
        )
        assert read_resp.status_code == 200

        # Banner 仍在
        banners = client.get("/api/messages/banners", headers=auth_headers).json()["data"]["items"]
        ids = [b["id"] for b in banners]
        assert created_broadcast in ids, "标已读不应让 Banner 消失"

        # 但 banner 的 is_read 字段为 True
        target = next(b for b in banners if b["id"] == created_broadcast)
        assert target["is_read"] is True


# ==================== 场景 4: 过期广播 ====================

class TestExpiredBroadcast:
    """场景 4: 过期广播不出现在 banners 与列表（"过期即失效"语义）。"""

    def test_expired_invisible(self, client: TestClient, admin_headers: dict, auth_headers: dict):
        # 创建一条 starts_at 在过去、ends_at 也在过去的广播
        now = datetime.now(timezone.utc)
        payload = {
            "level": "info",
            "title": "过期测试广播",
            "content": "should not be visible",
            "starts_at": (now - timedelta(hours=2)).isoformat(),
            "ends_at": (now - timedelta(hours=1)).isoformat(),
        }
        create = client.post("/api/admin/broadcasts", json=payload, headers=admin_headers)
        assert create.status_code in (200, 201), f"创建过期广播失败: {create.text}"
        bid = create.json()["data"]["id"]
        try:
            # banners 中不应包含
            banners = client.get("/api/messages/banners", headers=auth_headers).json()["data"]["items"]
            assert bid not in [b["id"] for b in banners], "过期广播不应出现在 banners"

            # 通知中心列表也不应包含
            inbox = client.get("/api/messages?page=1&page_size=200", headers=auth_headers).json()["data"]["items"]
            broadcast_refs = [m["ref_id"] for m in inbox if m.get("ref_type") == "broadcast"]
            assert bid not in broadcast_refs, "过期广播不应出现在通知中心"
        finally:
            client.delete(f"/api/admin/broadcasts/{bid}", headers=admin_headers)


# ==================== 场景 5: 软删除立即不可见 ====================

class TestSoftDelete:
    """场景 5: 撤回后广播立即对所有用户不可见。"""

    def test_soft_delete_hides_immediately(
        self,
        client: TestClient,
        admin_headers: dict,
        auth_headers: dict,
    ):
        payload = {"level": "warning", "title": "撤回测试", "content": "soon to be deleted"}
        create = client.post("/api/admin/broadcasts", json=payload, headers=admin_headers)
        assert create.status_code in (200, 201)
        bid = create.json()["data"]["id"]

        # 撤回前可见
        before = client.get("/api/messages/banners", headers=auth_headers).json()["data"]["items"]
        assert bid in [b["id"] for b in before]

        # 撤回
        delete_resp = client.delete(f"/api/admin/broadcasts/{bid}", headers=admin_headers)
        assert delete_resp.status_code == 200

        # 撤回后立即不可见
        after = client.get("/api/messages/banners", headers=auth_headers).json()["data"]["items"]
        assert bid not in [b["id"] for b in after], "撤回后 banner 应立即消失"

        inbox = client.get("/api/messages?page=1&page_size=200", headers=auth_headers).json()["data"]["items"]
        broadcast_refs = [m["ref_id"] for m in inbox if m.get("ref_type") == "broadcast"]
        assert bid not in broadcast_refs, "撤回后通知中心列表应立即消失"


# ==================== 场景 6: 非 admin 权限拒绝 ====================

class TestAdminOnly:
    """场景 6: 非管理员调用 /api/admin/broadcasts 返回 403。"""

    def test_create_requires_admin(self, client: TestClient, auth_headers: dict):
        resp = client.post(
            "/api/admin/broadcasts",
            json={"level": "info", "title": "非管理员尝试", "content": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_list_requires_admin(self, client: TestClient, auth_headers: dict):
        resp = client.get("/api/admin/broadcasts", headers=auth_headers)
        assert resp.status_code == 403

    def test_unauth_no_access(self, client: TestClient):
        resp = client.get("/api/admin/broadcasts")
        assert resp.status_code == 401


# ==================== 场景 7: dismissible=False 不可关闭 ====================

class TestNonDismissible:
    """场景 7: dismissible=False 的广播不允许用户关闭，返回 400。"""

    def test_non_dismissible_rejects(
        self,
        client: TestClient,
        admin_headers: dict,
        auth_headers: dict,
    ):
        payload = {
            "level": "critical",
            "title": "强制公告",
            "content": "不可关闭",
            "dismissible": False,
        }
        create = client.post("/api/admin/broadcasts", json=payload, headers=admin_headers)
        assert create.status_code in (200, 201)
        bid = create.json()["data"]["id"]
        try:
            resp = client.post(
                f"/api/messages/broadcasts/{bid}/dismiss",
                headers=auth_headers,
            )
            assert resp.status_code == 400, f"dismissible=False 时应拒绝关闭: {resp.text}"

            # 即使尝试关闭失败，banner 仍然可见且可被标已读
            banners = client.get("/api/messages/banners", headers=auth_headers).json()["data"]["items"]
            assert bid in [b["id"] for b in banners]
            target = next(b for b in banners if b["id"] == bid)
            assert target["dismissible"] is False
        finally:
            client.delete(f"/api/admin/broadcasts/{bid}", headers=admin_headers)


# ==================== 附加：Schema 校验 ====================

class TestBroadcastValidation:
    """请求体校验（Pydantic 层）"""

    def test_invalid_level_rejected(self, client: TestClient, admin_headers: dict):
        resp = client.post(
            "/api/admin/broadcasts",
            json={"level": "fatal", "title": "x", "content": "x"},
            headers=admin_headers,
        )
        assert resp.status_code == 422  # Pydantic pattern 校验失败

    def test_starts_after_ends_rejected(self, client: TestClient, admin_headers: dict):
        now = datetime.now(timezone.utc)
        resp = client.post(
            "/api/admin/broadcasts",
            json={
                "level": "info",
                "title": "时间窗口反向",
                "content": "x",
                "starts_at": now.isoformat(),
                "ends_at": (now - timedelta(hours=1)).isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
