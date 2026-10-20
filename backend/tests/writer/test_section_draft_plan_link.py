from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_save_draft_persists_section_plan_metadata(monkeypatch) -> None:
    from app.core.writer.composer import draft_persistence
    from app.models.writing_section_draft import WritingSectionDraft

    saved = {}

    class FakeDb:
        async def execute(self, *args, **kwargs):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        def add(self, obj):
            saved["draft"] = obj

        async def commit(self):
            pass

        async def refresh(self, obj):
            obj.id = 99

    composer = SimpleNamespace(
        db=FakeDb(),
        _db_lock=_AsyncNoopLock(),
        _serialize_source_ids=lambda value: "[]" if not value else str(value),
    )
    node = SimpleNamespace(id=3, title="服务方案")

    draft = await draft_persistence.save_draft(
        composer,
        node,
        "<p>正文</p>",
        section_plan_id=7,
        planning_mode="planned_new",
        planning_failure_code=None,
    )

    assert isinstance(draft, WritingSectionDraft)
    assert saved["draft"].section_plan_id == 7
    assert saved["draft"].planning_mode == "planned_new"
    assert saved["draft"].planning_failure_code is None


class _AsyncNoopLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False
