"""邮箱验证硬拦截 + 一次性域名 集成测试 (范围B)

用 TestClient(同步, 经 conftest 桥接)规避本机 asyncpg 事件循环问题;
monkeypatch 临时开启 EMAIL_VERIFICATION_ENABLED 验证灰度开关下行为, 并放开注册限流。
"""

import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.core.config import settings

PWD = "Pass1234"


def _uniq(prefix: str) -> str:
    _uniq._n = getattr(_uniq, "_n", 0) + 1
    return f"{prefix}{str(int(time.time()))[-6:]}{_uniq._n}"


def _install_capture(monkeypatch) -> list[tuple[str, str]]:
    """拦截验证邮件发送, 捕获 (收件邮箱, 验证链接)"""
    captured: list[tuple[str, str]] = []

    async def _cap(to_email: str, verify_url: str) -> bool:
        captured.append((to_email, verify_url))
        return True

    monkeypatch.setattr("app.services.auth_service.send_verification_email", _cap)
    return captured


def _token_from_url(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]



def _enable_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_REGISTER", "1000/minute")


def test_disposable_email_rejected(client: TestClient, monkeypatch):
    """开启验证时, 一次性邮箱域名注册被拒"""
    _enable_gate(monkeypatch)
    _install_capture(monkeypatch)
    u = _uniq("disp")
    resp = client.post("/api/auth/register", json={
        "username": u, "email": f"{u}@mailinator.com",
        "password": PWD, "confirm_password": PWD,
    })
    assert resp.status_code == 400


def test_gate_blocks_login_until_verified(client: TestClient, monkeypatch):
    """未验证不能登录; 验证后可以登录"""
    _enable_gate(monkeypatch)
    captured = _install_capture(monkeypatch)
    u = _uniq("verif")
    email = f"{u}@example.com"

    r = client.post("/api/auth/register", json={
        "username": u, "email": email, "password": PWD, "confirm_password": PWD,
    })
    assert r.status_code == 200

    # 未验证: 登录硬拦截 + 暂不发放试用
    lr = client.post("/api/auth/login", json={"username": u, "password": PWD})
    assert lr.status_code == 403

    # 取验证链接并验证
    url = dict(captured)[email]
    vr = client.post("/api/auth/verify-email", json={"token": _token_from_url(url)})
    assert vr.status_code == 200

    # 验证后: 登录通过 + 发放三项试用
    lr2 = client.post("/api/auth/login", json={"username": u, "password": PWD})
    assert lr2.status_code == 200


def test_verify_token_reuse_rejected(client: TestClient, monkeypatch):
    """验证令牌一次性: 复用同一 token 失败"""
    _enable_gate(monkeypatch)
    captured = _install_capture(monkeypatch)
    u = _uniq("reuse")
    email = f"{u}@example.com"
    client.post("/api/auth/register", json={
        "username": u, "email": email, "password": PWD, "confirm_password": PWD,
    })
    token = _token_from_url(dict(captured)[email])
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 400


def test_resend_verification_success(client: TestClient, monkeypatch):
    """重发验证邮件返回成功, 并再次发出邮件"""
    _enable_gate(monkeypatch)
    captured = _install_capture(monkeypatch)
    u = _uniq("resend")
    email = f"{u}@example.com"
    client.post("/api/auth/register", json={
        "username": u, "email": email, "password": PWD, "confirm_password": PWD,
    })
    resp = client.post("/api/auth/resend-verification", json={"email": email})
    assert resp.status_code == 200
    assert sum(1 for to, _ in captured if to == email) >= 2


def test_admin_created_user_can_login_when_email_gate_enabled(
    client: TestClient, admin_headers: dict, monkeypatch
):
    """管理员创建用户视为已背书, 邮箱验证开启时也应可直接登录"""
    _enable_gate(monkeypatch)
    u = _uniq("adminmade")
    email = f"{u}@example.com"

    create_resp = client.post(
        "/api/users",
        json={
            "username": u,
            "email": email,
            "password": PWD,
            "role": "user",
        },
        headers=admin_headers,
    )
    assert create_resp.status_code == 200, create_resp.text

    login_resp = client.post(
        "/api/auth/login",
        json={"username": u, "password": PWD},
    )
    assert login_resp.status_code == 200, login_resp.text
