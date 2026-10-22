"""任务级缓存隔离与负缓存契约。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.writer.composer.compose_cache import ComposeTaskCache


def test_cache_rejects_cross_project_context_access() -> None:
    cache = ComposeTaskCache(project_id=11)
    cache.set_project_context(11, ["clause-a"])
    with pytest.raises(ValueError, match="project-scoped"):
        cache.get_project_context(99)


def test_negative_draft_cache_hits_after_miss() -> None:
    cache = ComposeTaskCache(project_id=1)
    hit, value = cache.get_current_draft(8)
    assert hit is False
    assert value is None
    cache.set_current_draft(8, None)
    hit, value = cache.get_current_draft(8)
    assert hit is True
    assert value is None
    assert cache.misses["draft"] == 1
    assert cache.hits["draft"] == 1


def test_save_invalidates_current_draft() -> None:
    cache = ComposeTaskCache(project_id=1)
    cache.set_current_draft(4, SimpleNamespace(id=41, content="old"))
    cache.invalidate_current_draft(4)
    hit, value = cache.get_current_draft(4)
    assert hit is False
    assert value is None


def test_cache_on_off_source_fingerprint_identical() -> None:
    payload = SimpleNamespace(
        source_clause_ids=[1, 2],
        source_material_ids=[9],
        gate_flags={"x": {"passed": True}},
        planning_mode="planned_new",
        failure_state="completed",
    )
    on_cache = ComposeTaskCache(project_id=3)
    off_cache = ComposeTaskCache(project_id=3)
    assert on_cache.source_fingerprint(payload) == off_cache.source_fingerprint(payload)


def test_chunk_cache_disabled_records_miss_only() -> None:
    cache = ComposeTaskCache(project_id=5, chunk_cache_enabled=False)
    cache.record_chunk_access(hit=True)
    cache.record_chunk_access(hit=False)
    assert cache.hits["chunk"] == 0
    assert cache.misses["chunk"] == 2
