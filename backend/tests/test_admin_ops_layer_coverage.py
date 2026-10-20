"""R2-8 Phase 1 snapshot baseline: 4 个未覆盖 layer/聚合的 mock-DB 单测.

补齐 _layer_platform / _layer_throughput / _layer_ops / _business_health 的 name
集合 byte-equal 护栏,与 test_admin_ops_metric_semantics.py 风格一致。
"""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://bidmind:bidmind@localhost:5432/bidmind_lite",
)
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "CursorTest@2026_Strong")

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "api" / "routers" / "admin_ops.py"


def _load_admin_ops_module():
    spec = importlib.util.spec_from_file_location("test_admin_ops_layer_coverage_module", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResult:
    """Mock SQLAlchemy result supporting both .scalar() and .all() calls."""

    def __init__(self, scalar_value=None, rows=None):
        self._scalar = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows


class _FakeSession:
    """Mock AsyncSession yielding pre-canned _FakeResult per execute call.

    statements 字段保留每次 execute 的 SQL 文本表示，
    便于在测试中断言 WHERE 条件包含/不包含特定过滤(如 level IN ...)。
    """

    def __init__(self, results):
        self._results = list(results)
        self.statements: list[str] = []

    async def execute(self, statement):
        if not self._results:
            raise AssertionError("unexpected execute call: result queue empty")
        self.statements.append(str(statement))
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_layer_platform_returns_expected_metric_names(monkeypatch):
    """_layer_platform 应返回 4 个固定 name 的 metric (R2-8 契约)."""
    module = _load_admin_ops_module()

    import app.core.redis as redis_module

    async def _fake_get_redis_pool():
        return None

    monkeypatch.setattr(redis_module, "get_redis_pool", _fake_get_redis_pool)

    db = _FakeSession([
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
    ])

    metrics = await module._layer_platform(
        db,
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert [m["name"] for m in metrics] == [
        "unresolved_error_total",
        "critical_error_total",
        "redis_ready",
        "db_ready",
    ]
    assert all(m["layer"] == "platform" for m in metrics)


@pytest.mark.asyncio
async def test_layer_throughput_returns_expected_metric_names():
    """_layer_throughput 应返回 4 个固定 name 的 metric."""
    module = _load_admin_ops_module()

    db = _FakeSession([
        _FakeResult(rows=[]),
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0.0),
        _FakeResult(scalar_value=0),
    ])

    metrics = await module._layer_throughput(
        db,
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert [m["name"] for m in metrics] == [
        "task_active_gauge",
        "task_completed_total",
        "task_failed_total",
        "task_queue_wait_seconds_avg",
    ]
    assert all(m["layer"] == "throughput" for m in metrics)


@pytest.mark.asyncio
async def test_layer_platform_unresolved_filters_warning_out(monkeypatch):
    """护栏: unresolved_error_total 必须只统计 ERROR/CRITICAL,排除 WARNING.

    防止回归 — 历史上该指标包含 WARNING 级别,
    导致"未处理错误数"被警告噪音淹没,与"高优先级错误数"口径不一致。
    """
    module = _load_admin_ops_module()

    import app.core.redis as redis_module

    async def _fake_get_redis_pool():
        return None

    monkeypatch.setattr(redis_module, "get_redis_pool", _fake_get_redis_pool)

    db = _FakeSession([
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
    ])

    await module._layer_platform(
        db,
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert len(db.statements) >= 4, "_layer_platform 应至少执行 4 次错误日志查询"
    unresolved_cur_sql = db.statements[0]
    unresolved_base_sql = db.statements[1]
    assert "level IN" in unresolved_cur_sql or "level in" in unresolved_cur_sql.lower(), (
        f"unresolved_error_total current 查询未按 level 过滤,SQL: {unresolved_cur_sql}"
    )
    assert "level IN" in unresolved_base_sql or "level in" in unresolved_base_sql.lower(), (
        f"unresolved_error_total baseline 查询未按 level 过滤,SQL: {unresolved_base_sql}"
    )


@pytest.mark.asyncio
async def test_layer_ops_top_error_modules_filters_warning_out():
    """护栏: top_error_modules 必须只统计 ERROR/CRITICAL,排除 WARNING.

    与平台健康层口径保持一致 — WARNING 不应进入高频报错模块统计,
    否则模块计数会被警告噪音放大,失去定位真实故障模块的能力。
    """
    module = _load_admin_ops_module()

    db = _FakeSession([
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
        _FakeResult(rows=[]),
    ])

    await module._layer_ops(
        db,
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert len(db.statements) >= 3, "_layer_ops 应至少执行 3 次查询"
    top_modules_sql = db.statements[2]
    assert "level IN" in top_modules_sql or "level in" in top_modules_sql.lower(), (
        f"top_error_modules 查询未按 level 过滤,SQL: {top_modules_sql}"
    )


@pytest.mark.asyncio
async def test_layer_ops_returns_expected_metric_names():
    """_layer_ops 应返回 2 个固定 name 的 metric."""
    module = _load_admin_ops_module()

    db = _FakeSession([
        _FakeResult(scalar_value=0),
        _FakeResult(scalar_value=0),
        _FakeResult(rows=[]),
    ])

    metrics = await module._layer_ops(
        db,
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert [m["name"] for m in metrics] == [
        "audit_log_total_window",
        "top_error_modules",
    ]
    assert all(m["layer"] == "ops" for m in metrics)


@pytest.mark.asyncio
async def test_business_health_returns_three_business_keys():
    """_business_health 应返回三业务 key (tender/collusion/writer) 且字段结构 byte-equal."""
    module = _load_admin_ops_module()

    db = _FakeSession([
        _FakeResult(rows=[]),
        _FakeResult(rows=[]),
    ])

    result = await module._business_health(
        db,
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert set(result.keys()) == {"tender", "collusion", "writer"}
    expected_inner_keys = {"flow", "label", "active", "failed_in_window", "status"}
    for flow_key in ("tender", "collusion", "writer"):
        assert set(result[flow_key].keys()) == expected_inner_keys
        assert result[flow_key]["flow"] == flow_key
