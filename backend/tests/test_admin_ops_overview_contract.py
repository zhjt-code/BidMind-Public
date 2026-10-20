"""R2-8 Phase 1 snapshot baseline: /overview endpoint 顶层 schema 锚点.

验证 get_ops_overview 返回的 ApiResponse.data 顶层字段集 + layers/pillar_summary 子键,
确保 R2-8 拆分后 endpoint 响应 schema byte-equal。
"""
from __future__ import annotations

import importlib.util
import os
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
    spec = importlib.util.spec_from_file_location("test_admin_ops_overview_module", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeSession:
    """DB should not be touched when all layer/health/cost callables are mocked."""

    async def execute(self, _statement):
        raise AssertionError("DB should not be accessed when all layers are mocked")


def _patch_all_layers_empty(module, monkeypatch):
    """Replace 8 layers + business_health + cost_snapshot with empty fakes."""

    async def _empty_async(*_args, **_kwargs):
        return []

    def _empty_sync(*_args, **_kwargs):
        return []

    async def _empty_health(*_args, **_kwargs):
        return {"tender": {}, "collusion": {}, "writer": {}}

    async def _empty_cost(*_args, **_kwargs):
        return {}

    for name in ("_layer_platform", "_layer_throughput", "_layer_llm", "_layer_vector", "_layer_ops"):
        monkeypatch.setattr(module, name, _empty_async)
    for name in ("_layer_stage", "_layer_quality", "_layer_stability"):
        monkeypatch.setattr(module, name, _empty_sync)
    monkeypatch.setattr(module, "_business_health", _empty_health)
    monkeypatch.setattr(module, "_cost_snapshot", _empty_cost)


@pytest.mark.asyncio
async def test_overview_response_has_required_top_level_keys(monkeypatch):
    """/overview ApiResponse.data 顶层必须含 9 个 key (R2-8 byte-equal 契约)."""
    module = _load_admin_ops_module()
    _patch_all_layers_empty(module, monkeypatch)

    resp = await module.get_ops_overview(
        window="24h",
        _admin=None,
        db=_FakeSession(),
    )

    assert resp.code == 200
    data = resp.data
    assert set(data.keys()) == {
        "window",
        "window_start",
        "baseline_start",
        "generated_at",
        "layers",
        "pillar_summary",
        "business_health",
        "cost",
        "dispatcher",
    }


@pytest.mark.asyncio
async def test_overview_layers_contains_eight_named_keys(monkeypatch):
    """/overview data.layers 必须含 8 个 layer key (R2-8 byte-equal 契约)."""
    module = _load_admin_ops_module()
    _patch_all_layers_empty(module, monkeypatch)

    resp = await module.get_ops_overview(
        window="24h",
        _admin=None,
        db=_FakeSession(),
    )

    assert set(resp.data["layers"].keys()) == {
        "platform",
        "throughput",
        "stage",
        "quality",
        "llm",
        "vector",
        "stability",
        "ops",
    }


@pytest.mark.asyncio
async def test_overview_pillar_summary_contains_four_pillars(monkeypatch):
    """/overview data.pillar_summary 必须含 4 个 pillar key 与四个 verdict bucket."""
    module = _load_admin_ops_module()
    _patch_all_layers_empty(module, monkeypatch)

    resp = await module.get_ops_overview(
        window="24h",
        _admin=None,
        db=_FakeSession(),
    )

    pillar_summary = resp.data["pillar_summary"]
    assert set(pillar_summary.keys()) == {"quality", "reliability", "efficiency", "cost"}
    expected_buckets = {"improving", "stable", "warn", "critical"}
    for pillar in ("quality", "reliability", "efficiency", "cost"):
        assert set(pillar_summary[pillar].keys()) == expected_buckets


@pytest.mark.asyncio
async def test_overview_window_fallback_to_24h_when_unknown(monkeypatch):
    """非法 window 应回退到 24h (R2-8 已存在的行为契约)."""
    module = _load_admin_ops_module()
    _patch_all_layers_empty(module, monkeypatch)

    resp = await module.get_ops_overview(
        window="bogus",
        _admin=None,
        db=_FakeSession(),
    )

    assert resp.data["window"] == "24h"
