"""忘记密码 / 重置密码 集成测试

用 TestClient(同步, 经 conftest 桥接)规避本机 asyncpg 事件循环问题;
monkeypatch 放开注册/登录限流, 并拦截重置邮件发送以取出令牌。
"""

import time
from urllib.parse import urlparse, parse_qs

from fastapi.testclient import TestClient

from app.core.config import settings

PWD = "Pass1234"
NEWPWD = "NewPass5678"


def _uniq(prefix: str) -> str:
    _uniq._n = getattr(_uniq, "_n", 0) + 1
    return f"{prefix}{str(int(time.time()))[-6:]}{_uniq._n}"


def _relax_limits(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_REGISTER", "1000/minute")
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN", "1000/minute")
    # 忘记密码与邮箱验证开关无关; 固定关闭以保证 register->login 确定性
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_ENABLED", False)


def _capture(monkeypatch) -> list[tuple[str, str]]:
    captured: list[tuple[str, str]] = []

    async def _cap(to_email: str, reset_url: str) -> bool:
        captured.append((to_email, reset_url))
        return True

    monkeypatch.setattr("app.services.auth_service.send_password_reset_email", _cap)
    return captured


def _token_from_url(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


def _register(client: TestClient, username: str) -> str:
    email = f"{username}@example.com"
    r = client.post("/api/auth/register", json={
        "username": username, "email": email,
        "password": PWD, "confirm_password": PWD,
    })
    assert r.status_code == 200, r.text
    return email


def test_forgot_nonexistent_returns_200_no_mail(client: TestClient, monkeypatch):
    """不存在的邮箱也返回 200(防枚举), 且不发信"""
    _relax_limits(monkeypatch)
    captured = _capture(monkeypatch)
    r = client.post("/api/auth/forgot-password", json={
        "email": f"nobody{int(time.time())}@example.com",
    })
    assert r.status_code == 200
    assert captured == []


def test_reset_password_flow(client: TestClient, monkeypatch):
    """重置后新密码可登录、旧密码失效"""
    _relax_limits(monkeypatch)
    captured = _capture(monkeypatch)
    u = _uniq("rst")
    email = _register(client, u)

    assert client.post("/api/auth/forgot-password", json={"email": email}).status_code == 200
    token = _token_from_url(dict(captured)[email])

    r = client.post("/api/auth/reset-password", json={
        "token": token, "new_password": NEWPWD, "confirm_password": NEWPWD,
    })
    assert r.status_code == 200

    assert client.post("/api/auth/login", json={"username": u, "password": NEWPWD}).status_code == 200
    assert client.post("/api/auth/login", json={"username": u, "password": PWD}).status_code == 401


def test_reset_token_reuse_rejected(client: TestClient, monkeypatch):
    """重置令牌一次性: 复用同一 token 失败"""
    _relax_limits(monkeypatch)
    captured = _capture(monkeypatch)
    u = _uniq("rru")
    email = _register(client, u)
    client.post("/api/auth/forgot-password", json={"email": email})
    token = _token_from_url(dict(captured)[email])

    assert client.post("/api/auth/reset-password", json={
        "token": token, "new_password": NEWPWD, "confirm_password": NEWPWD,
    }).status_code == 200
    assert client.post("/api/auth/reset-password", json={
        "token": token, "new_password": NEWPWD, "confirm_password": NEWPWD,
    }).status_code == 400


def test_reset_expired_token_rejected(client: TestClient, monkeypatch):
    """过期令牌被拒(TTL 设为 -1 使其出生即过期)"""
    _relax_limits(monkeypatch)
    monkeypatch.setattr(settings, "PASSWORD_RESET_TOKEN_TTL_MINUTES", -1)
    captured = _capture(monkeypatch)
    u = _uniq("rex")
    email = _register(client, u)
    client.post("/api/auth/forgot-password", json={"email": email})
    token = _token_from_url(dict(captured)[email])

    assert client.post("/api/auth/reset-password", json={
        "token": token, "new_password": NEWPWD, "confirm_password": NEWPWD,
    }).status_code == 400


def test_reset_weak_password_rejected(client: TestClient, monkeypatch):
    """弱密码被 schema 校验拦截(422)"""
    _relax_limits(monkeypatch)
    captured = _capture(monkeypatch)
    u = _uniq("rwk")
    email = _register(client, u)
    client.post("/api/auth/forgot-password", json={"email": email})
    token = _token_from_url(dict(captured)[email])

    r = client.post("/api/auth/reset-password", json={
        "token": token, "new_password": "nodigits", "confirm_password": "nodigits",
    })
    assert r.status_code == 422


def test_old_jwt_invalidated_after_reset(client: TestClient, monkeypatch):
    """重置后该用户旧 access/refresh 令牌立即失效(token_version 强失效)"""
    _relax_limits(monkeypatch)
    captured = _capture(monkeypatch)
    u = _uniq("rjwt")
    email = _register(client, u)

    login = client.post("/api/auth/login", json={"username": u, "password": PWD})
    assert login.status_code == 200
    access = login.json()["data"]["access_token"]
    refresh = login.json()["data"]["refresh_token"]
    headers = {"Authorization": f"Bearer {access}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    client.post("/api/auth/forgot-password", json={"email": email})
    token = _token_from_url(dict(captured)[email])
    assert client.post("/api/auth/reset-password", json={
        "token": token, "new_password": NEWPWD, "confirm_password": NEWPWD,
    }).status_code == 200

    # 旧 access / refresh 令牌均失效
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": refresh}).status_code == 401


def test_reset_clears_lockout(client: TestClient, monkeypatch):
    """连续错误锁定后, 重置密码顺带解锁, 新密码可登录"""
    _relax_limits(monkeypatch)
    captured = _capture(monkeypatch)
    u = _uniq("rlk")
    email = _register(client, u)

    for _ in range(5):
        client.post("/api/auth/login", json={"username": u, "password": "WrongX1"})
    # 已锁定: 即使密码正确也被拒
    assert client.post("/api/auth/login", json={"username": u, "password": PWD}).status_code == 403

    client.post("/api/auth/forgot-password", json={"email": email})
    token = _token_from_url(dict(captured)[email])
    assert client.post("/api/auth/reset-password", json={
        "token": token, "new_password": NEWPWD, "confirm_password": NEWPWD,
    }).status_code == 200

    # 重置后解锁, 新密码登录成功
    assert client.post("/api/auth/login", json={"username": u, "password": NEWPWD}).status_code == 200
