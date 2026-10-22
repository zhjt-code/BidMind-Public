"""认证接口集成测试

覆盖: 注册、登录、Token 刷新、获取当前用户。
"""

import time

from fastapi.testclient import TestClient

_TS = str(int(time.time()))[-6:]


class TestRegister:

    def test_register_success(self, client: TestClient):
        resp = client.post("/api/auth/register", json={
            "username": f"regok{_TS}",
            "email": f"regok{_TS}@example.com",
            "password": "Pass1234",
            "confirm_password": "Pass1234",
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["role"] == "user"

    def test_register_does_not_expose_private_billing_routes(self, client: TestClient):
        username = f"trialonly{_TS}"
        password = "Pass1234"
        resp = client.post("/api/auth/register", json={
            "username": username,
            "email": f"{username}@example.com",
            "password": password,
            "confirm_password": password,
        })
        assert resp.status_code == 200

        login_resp = client.post("/api/auth/login", json={
            "username": username,
            "password": password,
        })
        assert login_resp.status_code == 200
        token = login_resp.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        wallet_resp = client.get("/api/billing/wallet", headers=headers)
        assert wallet_resp.status_code == 404

        trial_resp = client.get("/api/billing/free-trial", headers=headers)
        assert trial_resp.status_code == 404

    def test_register_duplicate_username(self, client: TestClient, registered_user):
        resp = client.post("/api/auth/register", json={
            "username": registered_user["username"],
            "email": f"dup{_TS}@example.com",
            "password": "Pass1234",
            "confirm_password": "Pass1234",
        })
        assert resp.status_code == 400

    def test_register_password_mismatch(self, client: TestClient):
        resp = client.post("/api/auth/register", json={
            "username": f"mismatch{_TS}",
            "email": f"mismatch{_TS}@example.com",
            "password": "Pass1234",
            "confirm_password": "Different1234",
        })
        assert resp.status_code == 422

    def test_register_weak_password(self, client: TestClient):
        resp = client.post("/api/auth/register", json={
            "username": f"weak{_TS}",
            "email": f"weak{_TS}@example.com",
            "password": "nodigits",
            "confirm_password": "nodigits",
        })
        assert resp.status_code == 422


class TestLogin:

    def test_login_success(self, client: TestClient, registered_user):
        resp = client.post("/api/auth/login", json={
            "username": registered_user["username"],
            "password": registered_user["password"],
        })
        assert resp.status_code == 200
        tokens = resp.json()["data"]
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert tokens["token_type"] == "bearer"

    def test_login_wrong_password(self, client: TestClient, registered_user):
        resp = client.post("/api/auth/login", json={
            "username": registered_user["username"],
            "password": "WrongPassword1",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client: TestClient):
        resp = client.post("/api/auth/login", json={
            "username": "nobody_here",
            "password": "Pass1234",
        })
        assert resp.status_code == 401


class TestTokenRefresh:

    def test_refresh_token_success(self, client: TestClient, registered_user):
        login_resp = client.post("/api/auth/login", json={
            "username": registered_user["username"],
            "password": registered_user["password"],
        })
        refresh_token = login_resp.json()["data"]["refresh_token"]

        resp = client.post("/api/auth/refresh", json={
            "refresh_token": refresh_token,
        })
        assert resp.status_code == 200
        new_tokens = resp.json()["data"]
        assert "access_token" in new_tokens

    def test_refresh_with_invalid_token(self, client: TestClient):
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": "invalid.jwt.token",
        })
        assert resp.status_code == 401


class TestGetMe:

    def test_get_me_authenticated(self, client: TestClient, auth_headers, registered_user):
        resp = client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["username"] == registered_user["username"]

    def test_get_me_unauthenticated(self, client: TestClient):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401
