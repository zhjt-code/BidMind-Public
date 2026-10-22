import pytest


@pytest.mark.asyncio
async def test_with_task_prompt_snapshot_resets_token(monkeypatch) -> None:
    from app.core.ai import task_prompt_snapshot

    calls: list[tuple[str, object]] = []

    class FakeSnapshot:
        def reset(self, token):
            calls.append(("reset", token))

    monkeypatch.setattr(task_prompt_snapshot, "current_snapshot", FakeSnapshot())
    monkeypatch.setattr(
        task_prompt_snapshot,
        "install_task_snapshot",
        lambda flow: calls.append(("install", flow)) or "token-1",
    )

    @task_prompt_snapshot.with_task_prompt_snapshot("writing")
    async def target(value: int) -> int:
        return value + 1

    assert await target(41) == 42
    assert calls == [("install", "writing"), ("reset", "token-1")]
