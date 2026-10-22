"""The mixed TestClient/pytest event loops must not share asyncpg connections."""

from sqlalchemy.pool import NullPool

from app.core.database import engine


def test_regression_suite_uses_unpooled_connections() -> None:
    assert isinstance(engine.sync_engine.pool, NullPool)
