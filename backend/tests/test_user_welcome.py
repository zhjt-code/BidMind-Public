"""用户欢迎信确认接口防御测试。

覆盖场景：
1. 新用户 GET /auth/me 显示 welcome_letter_acknowledged_at=None
2. PUT 确认接口持久化且幂等（重复确认保持首次时间）
3. 未登录调用确认接口返回 401
4. 用户 A 确认后，用户 B 的状态不受影响（账号级隔离）
"""

import secrets

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

# ==================== 限流隔离 ====================


@pytest.fixture(autouse=True)
def _relax_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """在欢迎信测试期间将注册/登录限流设为 1000/minute。

    SlowAPI 以 limit 字符串为 bucket key 的一部分，切换到 "1000/minute" 后，
    欢迎信测试的注册/登录请求进入独立 bucket，不消耗原有 "5/minute" 计数器，
    避免污染后续 test_api_auth 的限流状态，参考 test_fingerprint_trial_cap.py。
    """
    monkeypatch.setattr(settings, "RATE_LIMIT_REGISTER", "1000/minute")
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN", "1000/minute")


# ==================== 辅助 fixtures ====================


@pytest.fixture()
def fresh_user_headers(client: TestClient, _relax_rate_limits: None) -> dict:
    """注册一个全新未确认欢迎信的用户，返回认证头。"""
    tag = f"w{secrets.token_hex(5)}"
    user = {
        "username": f"u{tag}",
        "email": f"u{tag}@example.com",
        "password": "TestPass@123",
        "confirm_password": "TestPass@123",
    }
    reg = client.post("/api/auth/register", json=user)
    assert reg.status_code == 200, f"注册失败: {reg.text}"
    login = client.post(
        "/api/auth/login",
        json={"username": user["username"], "password": user["password"]},
    )
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def second_fresh_user_headers(client: TestClient, _relax_rate_limits: None) -> dict:
    """注册第二个全新用户，与 fresh_user_headers 独立，用于隔离测试。

    创建方式参考 test_broadcasts.py second_user_headers，使用唯一随机标识。
    """
    tag = f"x{secrets.token_hex(5)}"
    user = {
        "username": f"u{tag}",
        "email": f"u{tag}@example.com",
        "password": "TestPass@123",
        "confirm_password": "TestPass@123",
    }
    reg = client.post("/api/auth/register", json=user)
    assert reg.status_code == 200, f"注册第二用户失败: {reg.text}"
    login = client.post(
        "/api/auth/login",
        json={"username": user["username"], "password": user["password"]},
    )
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ==================== 测试用例 ====================


def test_unacknowledged_user_is_exposed_by_get_me(
    client: TestClient,
    fresh_user_headers: dict,
) -> None:
    """新注册用户的 GET /auth/me 必须暴露 welcome_letter_acknowledged_at=None。"""
    response = client.get("/api/auth/me", headers=fresh_user_headers)
    assert response.status_code == 200
    assert response.json()["data"]["welcome_letter_acknowledged_at"] is None


def test_acknowledgement_is_persisted_and_idempotent(
    client: TestClient,
    fresh_user_headers: dict,
) -> None:
    """确认接口写入时间后幂等：重复调用返回相同的首次确认时间。"""
    first = client.put(
        "/api/auth/me/welcome-letter-acknowledgement",
        headers=fresh_user_headers,
    )
    assert first.status_code == 200
    first_at = first.json()["data"]["welcome_letter_acknowledged_at"]
    assert first_at, "首次确认后时间戳不应为空"

    second = client.put(
        "/api/auth/me/welcome-letter-acknowledgement",
        headers=fresh_user_headers,
    )
    assert second.status_code == 200
    assert (
        second.json()["data"]["welcome_letter_acknowledged_at"] == first_at
    ), "重复确认必须保持首次时间不变"

    refreshed = client.get("/api/auth/me", headers=fresh_user_headers)
    assert (
        refreshed.json()["data"]["welcome_letter_acknowledged_at"] == first_at
    ), "GET /auth/me 应返回已写入的确认时间"


def test_acknowledgement_requires_authentication(client: TestClient) -> None:
    """未携带 Token 调用确认接口应返回 401。"""
    response = client.put("/api/auth/me/welcome-letter-acknowledgement")
    assert response.status_code == 401


def test_acknowledgement_of_user_a_does_not_affect_user_b(
    client: TestClient,
    fresh_user_headers: dict,
    second_fresh_user_headers: dict,
) -> None:
    """用户 A 确认欢迎信后，用户 B 的 welcome_letter_acknowledged_at 仍为 None（账号级隔离）。"""
    # 确认 B 的初始状态为 None
    b_before = client.get("/api/auth/me", headers=second_fresh_user_headers)
    assert b_before.status_code == 200
    assert (
        b_before.json()["data"]["welcome_letter_acknowledged_at"] is None
    ), "用户 B 初始状态应为 None"

    # A 确认欢迎信
    ack = client.put(
        "/api/auth/me/welcome-letter-acknowledgement",
        headers=fresh_user_headers,
    )
    assert ack.status_code == 200
    assert ack.json()["data"]["welcome_letter_acknowledged_at"] is not None

    # B 的状态不受 A 确认影响
    b_after = client.get("/api/auth/me", headers=second_fresh_user_headers)
    assert b_after.status_code == 200
    assert (
        b_after.json()["data"]["welcome_letter_acknowledged_at"] is None
    ), "用户 A 确认后，用户 B 的状态不应改变"
