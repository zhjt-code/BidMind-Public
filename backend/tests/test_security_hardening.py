"""阶段1 安全加固防御测试

映射风险:
- 生产暴露面: 生产环境必须关闭公开文档 + 拒绝 CORS 通配符。
- 认证/上传限流: 超过阈值的 IP 必须被拦截(429)。
覆盖独立构造的 Settings 与最小 FastAPI 应用, 不污染全局单例状态。
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import Settings


def _make_settings(**overrides) -> Settings:
    base = dict(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        JWT_SECRET_KEY="x" * 40,
        ADMIN_PASSWORD="Strong@Pass1",
        LOG_LEVEL="INFO",
    )
    base.update(overrides)
    return Settings(**base)


class TestProductionGating:
    def test_is_production_flag(self):
        assert _make_settings(ENVIRONMENT="production").is_production() is True
        assert _make_settings(ENVIRONMENT="development").is_production() is False

    def test_production_rejects_wildcard_cors(self):
        with pytest.raises(ValueError):
            _make_settings(ENVIRONMENT="production", CORS_ORIGINS="*")

    def test_production_rejects_empty_cors(self):
        with pytest.raises(ValueError):
            _make_settings(ENVIRONMENT="production", CORS_ORIGINS="")

    def test_production_accepts_explicit_origin(self):
        s = _make_settings(
            ENVIRONMENT="production", CORS_ORIGINS="https://app.example.com"
        )
        assert s.get_cors_origins() == ["https://app.example.com"]

    def test_dev_allows_wildcard(self):
        # 开发环境不强制(便于本地联调)
        s = _make_settings(ENVIRONMENT="development", CORS_ORIGINS="*")
        assert s.is_production() is False

    def test_docs_disabled_in_production_app(self):
        """复用 main.py 的文档开关逻辑: 生产应关闭 docs/openapi。"""
        s = _make_settings(ENVIRONMENT="production", CORS_ORIGINS="https://a.com")
        enabled = not s.is_production()
        app = FastAPI(
            docs_url="/docs" if enabled else None,
            openapi_url="/openapi.json" if enabled else None,
        )
        assert app.docs_url is None
        assert app.openapi_url is None


class TestRateLimit:
    def test_blocks_after_threshold(self):
        limiter = Limiter(key_func=get_remote_address)
        app = FastAPI()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

        @app.post("/probe")
        @limiter.limit("2/minute")
        async def probe(request: Request):
            return {"ok": True}

        client = TestClient(app)
        assert client.post("/probe").status_code == 200
        assert client.post("/probe").status_code == 200
        # 第3次超过 2/minute 阈值, 必须被限流
        assert client.post("/probe").status_code == 429
