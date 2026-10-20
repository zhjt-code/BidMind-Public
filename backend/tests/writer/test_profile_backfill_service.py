"""章节画像回填服务契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ServiceError
from app.models.project import ProjectStatus
from app.services.writing.profile_backfill_service import ProfileBackfillService


@pytest.mark.asyncio
async def test_regenerate_rejects_when_status_not_outlined() -> None:
    project = SimpleNamespace(status=ProjectStatus.EXTRACTED, id=1)

    async def _resolve(*_a, **_kw):
        return project

    svc = ProfileBackfillService(db=AsyncMock(), project_resolver=_resolve)
    with pytest.raises(ServiceError, match="不允许回填"):
        await svc.regenerate_chapter_profiles(1, user=SimpleNamespace())


@pytest.mark.asyncio
async def test_regenerate_rejects_when_no_leaf_nodes(monkeypatch) -> None:
    project = SimpleNamespace(status=ProjectStatus.OUTLINED, id=2)

    async def _resolve(*_a, **_kw):
        return project

    svc = ProfileBackfillService(db=AsyncMock(), project_resolver=_resolve)
    monkeypatch.setattr(svc, "_count_leaf_nodes", AsyncMock(return_value=0))
    with pytest.raises(ServiceError, match="尚无目录叶子"):
        await svc.regenerate_chapter_profiles(2, user=SimpleNamespace())


@pytest.mark.asyncio
async def test_regenerate_idempotent_happy_path(monkeypatch) -> None:
    project = SimpleNamespace(status=ProjectStatus.OUTLINED, id=3)

    async def _resolve(*_a, **_kw):
        return project

    fake_generator = SimpleNamespace(
        generate_chapter_profiles=AsyncMock(return_value=4),
    )
    monkeypatch.setattr(
        "app.services.writing.profile_backfill_service.get_outline_generator",
        lambda **_kw: fake_generator,
    )
    monkeypatch.setattr(
        "app.services.writing.profile_backfill_service.MaterialMatcher._assess_chapter_profile_quality",
        staticmethod(
            lambda _nodes: {
                "total": 4,
                "ok": 4,
                "missing": 0,
                "weak": 0,
                "degraded": 0,
                "complete": 3,
                "matchable": 1,
            }
        ),
    )

    svc = ProfileBackfillService(db=AsyncMock(), project_resolver=_resolve)
    monkeypatch.setattr(svc, "_count_leaf_nodes", AsyncMock(return_value=4))
    monkeypatch.setattr(svc, "_load_outline_nodes", AsyncMock(return_value=[]))

    result = await svc.regenerate_chapter_profiles(3, user=SimpleNamespace())
    assert result["profiled"] == 4
    assert result["leaf_total"] == 4
    assert result["quality_stats"]["ok"] == 4
    fake_generator.generate_chapter_profiles.assert_awaited_once_with(3)
