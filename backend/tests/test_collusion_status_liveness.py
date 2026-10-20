from __future__ import annotations

import asyncio

import pytest


def test_collusion_liveness_timeout_covers_long_cloud_compare(monkeypatch) -> None:
    from app.core.config import settings
    from app.services.collusion_status_service import get_collusion_liveness_timeout

    monkeypatch.setattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180)
    monkeypatch.setattr(settings, "COLLUSION_COMPARE_TIMEOUT_CLOUD", 300)

    timeout = get_collusion_liveness_timeout()

    assert timeout >= 600
    assert timeout >= settings.COLLUSION_COMPARE_TIMEOUT_CLOUD + 120


@pytest.mark.asyncio
async def test_llm_keepalive_refreshes_heartbeat_while_waiting() -> None:
    from app.core.collusion.llm_keepalive import await_with_collusion_keepalive

    heartbeats: list[tuple[str, str]] = []

    def heartbeat(stage: str, progress: str, extra=None) -> None:
        heartbeats.append((stage, progress))

    async def slow_result():
        await asyncio.sleep(0.03)
        return "ok"

    result = await await_with_collusion_keepalive(
        slow_result(),
        heartbeat=heartbeat,
        stage="S4",
        progress="群组研判中",
        interval=0.01,
    )

    assert result == "ok"
    assert len(heartbeats) >= 2
    assert all(stage == "S4" for stage, _ in heartbeats)
