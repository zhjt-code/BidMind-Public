import pytest


@pytest.mark.asyncio
async def test_run_with_prompt_snapshot_skips_load_when_registry_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import worker
    from app.core.ai import prompt_registry as prompt_registry_mod
    from app.core import database as database_mod

    events: list[str] = []

    class _FakeRegistry:
        is_loaded = True

        async def load(self, _db):
            events.append("registry_load")

    class _FakeSnapshot:
        async def __aenter__(self):
            events.append("snapshot_enter")

        async def __aexit__(self, exc_type, exc, tb):
            events.append("snapshot_exit")

    async def _operation() -> None:
        events.append("operation")

    def _unexpected_db_session():
        events.append("db_session")
        raise AssertionError("loaded registry must not open a DB session")

    monkeypatch.setattr(database_mod, "AsyncSessionLocal", _unexpected_db_session)
    monkeypatch.setattr(prompt_registry_mod, "get_registry", lambda: _FakeRegistry())
    monkeypatch.setattr(prompt_registry_mod, "bind_task_snapshot", lambda _flow: _FakeSnapshot())

    await worker._run_with_prompt_snapshot("writing", _operation)

    assert events == [
        "snapshot_enter",
        "operation",
        "snapshot_exit",
    ]


@pytest.mark.asyncio
async def test_run_with_prompt_snapshot_loads_once_when_registry_is_unloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import worker
    from app.core.ai import prompt_registry as prompt_registry_mod
    from app.core import database as database_mod

    events: list[str] = []

    class _FakeDbSession:
        async def __aenter__(self):
            events.append("db_enter")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events.append("db_exit")

    class _FakeRegistry:
        is_loaded = False

        async def load(self, _db):
            events.append("registry_load")
            self.is_loaded = True

    class _FakeSnapshot:
        async def __aenter__(self):
            events.append("snapshot_enter")

        async def __aexit__(self, exc_type, exc, tb):
            events.append("snapshot_exit")

    async def _operation() -> None:
        events.append("operation")

    fake_registry = _FakeRegistry()
    monkeypatch.setattr(database_mod, "AsyncSessionLocal", lambda: _FakeDbSession())
    monkeypatch.setattr(prompt_registry_mod, "get_registry", lambda: fake_registry)
    monkeypatch.setattr(prompt_registry_mod, "bind_task_snapshot", lambda _flow: _FakeSnapshot())

    await worker._run_with_prompt_snapshot("writing", _operation)

    assert events == [
        "db_enter",
        "registry_load",
        "db_exit",
        "snapshot_enter",
        "operation",
        "snapshot_exit",
    ]
