"""主动改密会话失效 防御测试

锁定真实安全弱点(代码审查确证): change_password / update_user 改密后必须自增
token_version, 令此前签发的旧 JWT 立即失效, 否则用户主动改密无法踢掉被盗会话。
方案A: change-password 额外重签并返回新令牌, 使发起改密的设备无感保持登录。

复用 test_password_reset.py 的 TestClient 同步桥接与限流放开模式。
"""

import time

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
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_ENABLED", False)


def _register_and_login(client: TestClient, username: str) -> dict[str, str]:
    """注册并登录, 返回 access/refresh 令牌。"""
    email = f"{username}@example.com"
    r = client.post("/api/auth/register", json={
        "username": username, "email": email,
        "password": PWD, "confirm_password": PWD,
    })
    assert r.status_code == 200, r.text
    login = client.post("/api/auth/login", json={"username": username, "password": PWD})
    assert login.status_code == 200, login.text
    data = login.json()["data"]
    return {"access": data["access_token"], "refresh": data["refresh_token"]}


def test_change_password_invalidates_old_jwt_and_returns_new(client: TestClient, monkeypatch):
    """核心: 改密后旧 access/refresh 立即失效, 且返回的新令牌当前设备可用。"""
    _relax_limits(monkeypatch)
    u = _uniq("cpw")
    tokens = _register_and_login(client, u)
    old_headers = {"Authorization": f"Bearer {tokens['access']}"}
    assert client.get("/api/auth/me", headers=old_headers).status_code == 200

    # 用旧 access 发起改密, 应成功并返回新令牌对
    r = client.post("/api/auth/change-password", json={
        "current_password": PWD, "new_password": NEWPWD, "confirm_password": NEWPWD,
    }, headers=old_headers)
    assert r.status_code == 200, r.text
    new = r.json()["data"]
    assert new["access_token"] and new["refresh_token"]

    # 其他设备的旧 access / refresh 立即失效(token_version 强失效)
    assert client.get("/api/auth/me", headers=old_headers).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh"]}).status_code == 401

    # 当前设备凭新令牌无感保持登录
    new_headers = {"Authorization": f"Bearer {new['access_token']}"}
    assert client.get("/api/auth/me", headers=new_headers).status_code == 200
    assert client.post("/api/auth/refresh", json={"refresh_token": new["refresh_token"]}).status_code == 200

    # 新密码可登录, 旧密码失效
    assert client.post("/api/auth/login", json={"username": u, "password": NEWPWD}).status_code == 200
    assert client.post("/api/auth/login", json={"username": u, "password": PWD}).status_code == 401


def test_change_password_wrong_current_keeps_session(client: TestClient, monkeypatch):
    """边界/异常: 当前密码错误时拒绝, 且不得改动密码或踢掉当前会话。"""
    _relax_limits(monkeypatch)
    u = _uniq("cpwx")
    tokens = _register_and_login(client, u)
    headers = {"Authorization": f"Bearer {tokens['access']}"}

    r = client.post("/api/auth/change-password", json={
        "current_password": "WrongX1", "new_password": NEWPWD, "confirm_password": NEWPWD,
    }, headers=headers)
    assert r.status_code == 400, r.text

    # token_version 未自增: 原会话仍有效, 原密码仍可登录
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/login", json={"username": u, "password": PWD}).status_code == 200


def test_update_me_password_invalidates_old_jwt(client: TestClient, monkeypatch):
    """同源修复: 经 PUT /auth/me 改密同样应使旧 JWT 失效(覆盖 update_user 分支)。"""
    _relax_limits(monkeypatch)
    u = _uniq("upw")
    tokens = _register_and_login(client, u)
    headers = {"Authorization": f"Bearer {tokens['access']}"}

    r = client.put("/api/auth/me", json={
        "current_password": PWD, "new_password": NEWPWD,
    }, headers=headers)
    assert r.status_code == 200, r.text

    # 旧 token 失效, 新密码可登录
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.post("/api/auth/login", json={"username": u, "password": NEWPWD}).status_code == 200


def test_update_me_email_only_keeps_session(client: TestClient, monkeypatch):
    """回归: 仅改邮箱(不涉及密码)不应自增 token_version, 当前会话保持有效。"""
    _relax_limits(monkeypatch)
    u = _uniq("upe")
    tokens = _register_and_login(client, u)
    headers = {"Authorization": f"Bearer {tokens['access']}"}

    r = client.put("/api/auth/me", json={"email": f"{u}_new@example.com"}, headers=headers)
    assert r.status_code == 200, r.text

    # 未改密 -> 旧 token 仍有效
    assert client.get("/api/auth/me", headers=headers).status_code == 200
