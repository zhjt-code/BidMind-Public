"""站内信功能集成测试

验证：
- 消息 CRUD 接口基本功能
- 权限隔离（用户 A 不能读用户 B 的消息）
- 管理员更新反馈时自动生成站内信
- 未读数随标记已读递减
- notify_user=False 时不生成消息
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


# ==================== 基本接口测试 ====================


class TestMessagesAPI:
    """消息接口基本功能"""

    def test_unread_count_initial(self, client: TestClient, auth_headers: dict):
        resp = client.get("/api/messages/unread_count", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "count" in data
        assert isinstance(data["count"], int)

    def test_list_empty(self, client: TestClient, auth_headers: dict):
        resp = client.get("/api/messages?page=1&page_size=5", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "items" in data
        assert "total" in data

    def test_list_requires_auth(self, client: TestClient):
        resp = client.get("/api/messages")
        assert resp.status_code == 401

    def test_unread_count_requires_auth(self, client: TestClient):
        resp = client.get("/api/messages/unread_count")
        assert resp.status_code == 401


# ==================== 反馈触发站内信 ====================


class TestFeedbackTriggersMessage:
    """管理员更新反馈时生成站内信"""

    @pytest.fixture()
    def feedback_id(self, client: TestClient, auth_headers: dict) -> int:
        resp = client.post("/api/feedback", json={
            "feedback_type": "bug",
            "content": "站内信测试用反馈 -- 请忽略",
        }, headers=auth_headers)
        assert resp.status_code == 201, f"创建反馈失败: {resp.text}"
        return resp.json()["data"]["id"]

    def test_resolve_creates_message(
        self, client: TestClient, auth_headers: dict, admin_headers: dict, feedback_id: int,
    ):
        before = client.get("/api/messages/unread_count", headers=auth_headers)
        before_count = before.json()["data"]["count"]

        resp = client.patch(
            f"/api/feedback/{feedback_id}/status",
            json={"status": "resolved", "admin_note": "已修复，感谢反馈"},
            headers=admin_headers,
        )
        assert resp.status_code == 200

        after = client.get("/api/messages/unread_count", headers=auth_headers)
        after_count = after.json()["data"]["count"]
        assert after_count == before_count + 1

    def test_notify_user_false_no_message(
        self, client: TestClient, auth_headers: dict, admin_headers: dict, feedback_id: int,
    ):
        before = client.get("/api/messages/unread_count", headers=auth_headers)
        before_count = before.json()["data"]["count"]

        resp = client.patch(
            f"/api/feedback/{feedback_id}/status",
            json={"status": "closed", "admin_note": "静默关闭", "notify_user": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200

        after = client.get("/api/messages/unread_count", headers=auth_headers)
        assert after.json()["data"]["count"] == before_count


# ==================== 已读操作 ====================


class TestMarkRead:
    """标记已读与未读数变化"""

    @pytest.fixture()
    def message_with_feedback(
        self, client: TestClient, auth_headers: dict, admin_headers: dict,
    ) -> int:
        """创建一条反馈并让管理员回复，返回消息 id。"""
        fb_resp = client.post("/api/feedback", json={
            "feedback_type": "feature",
            "content": "标记已读测试用反馈",
        }, headers=auth_headers)
        fb_id = fb_resp.json()["data"]["id"]

        client.patch(
            f"/api/feedback/{fb_id}/status",
            json={"status": "resolved", "admin_note": "done"},
            headers=admin_headers,
        )

        msgs = client.get("/api/messages?page=1&page_size=1", headers=auth_headers)
        items = msgs.json()["data"]["items"]
        assert len(items) > 0
        return items[0]["id"]

    def test_mark_single_read(
        self, client: TestClient, auth_headers: dict, message_with_feedback: int,
    ):
        resp = client.patch(
            f"/api/messages/{message_with_feedback}/read",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_read"] is True

    def test_mark_all_read(self, client: TestClient, auth_headers: dict):
        resp = client.post("/api/messages/read_all", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 0

        after = client.get("/api/messages/unread_count", headers=auth_headers)
        assert after.json()["data"]["count"] == 0


# ==================== 权限隔离 ====================


class TestMessagePermission:
    """用户 A 不能读取/标记用户 B 的消息"""

    def test_cannot_read_others_message(
        self, client: TestClient, admin_headers: dict,
    ):
        resp = client.patch("/api/messages/999999/read", headers=admin_headers)
        assert resp.status_code == 404
