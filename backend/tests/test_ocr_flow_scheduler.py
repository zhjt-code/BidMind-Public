"""OCR flow-level fair scheduler contract tests."""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)


@pytest.mark.asyncio
async def test_single_flow_can_use_full_capacity() -> None:
    from app.core.parser.ocr_flow_scheduler import OcrFlowScheduler

    scheduler = OcrFlowScheduler(contended_max_share_ratio=0.6)

    for _ in range(5):
        await scheduler.acquire("project:1", "full_page", capacity=5)

    assert scheduler.get_stats()["flows"]["project:1"]["inflight"] == 5

    for _ in range(5):
        await scheduler.release("project:1", "full_page")


@pytest.mark.asyncio
async def test_two_flows_converge_to_sixty_forty_share() -> None:
    from app.core.parser.ocr_flow_scheduler import OcrFlowScheduler

    scheduler = OcrFlowScheduler(contended_max_share_ratio=0.6)
    for _ in range(5):
        await scheduler.acquire("project:old", "full_page", capacity=5)

    old_wait = asyncio.create_task(
        scheduler.acquire("project:old", "full_page", capacity=5)
    )
    new_wait = asyncio.create_task(
        scheduler.acquire("project:new", "full_page", capacity=5)
    )
    await asyncio.sleep(0)
    assert not old_wait.done()
    assert not new_wait.done()

    await scheduler.release("project:old", "full_page")
    await asyncio.wait_for(new_wait, timeout=1)
    assert not old_wait.done()

    stats = scheduler.get_stats()["flows"]
    assert stats["project:old"]["inflight"] == 4
    assert stats["project:new"]["inflight"] == 1

    old_wait.cancel()
    await asyncio.gather(old_wait, return_exceptions=True)


@pytest.mark.asyncio
async def test_flow_can_borrow_idle_capacity_and_return_it_on_contention() -> None:
    from app.core.parser.ocr_flow_scheduler import OcrFlowScheduler

    scheduler = OcrFlowScheduler(contended_max_share_ratio=0.6)
    for _ in range(5):
        await scheduler.acquire("project:old", "full_page", capacity=5)

    await scheduler.release("project:old", "full_page")
    await scheduler.acquire("project:old", "full_page", capacity=5)
    assert scheduler.get_stats()["flows"]["project:old"]["inflight"] == 5

    new_wait = asyncio.create_task(
        scheduler.acquire("project:new", "full_page", capacity=5)
    )
    await asyncio.sleep(0)
    assert not new_wait.done()

    await scheduler.release("project:old", "full_page")
    await asyncio.wait_for(new_wait, timeout=1)

    stats = scheduler.get_stats()["flows"]
    assert stats["project:old"]["inflight"] == 4
    assert stats["project:new"]["inflight"] == 1


@pytest.mark.asyncio
async def test_per_image_waiter_preempts_full_page_within_same_flow() -> None:
    from app.core.parser.ocr_flow_scheduler import OcrFlowScheduler

    scheduler = OcrFlowScheduler(contended_max_share_ratio=0.6)
    await scheduler.acquire("project:1", "full_page", capacity=1)

    full_wait = asyncio.create_task(
        scheduler.acquire("project:1", "full_page", capacity=1)
    )
    per_wait = asyncio.create_task(
        scheduler.acquire("project:1", "per_image", capacity=1)
    )
    await asyncio.sleep(0)

    await scheduler.release("project:1", "full_page")
    await asyncio.wait_for(per_wait, timeout=1)
    assert not full_wait.done()

    full_wait.cancel()
    await asyncio.gather(full_wait, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_primary_waiter_wakes_secondary_waiter() -> None:
    from app.core.parser.ocr_flow_scheduler import OcrFlowScheduler

    scheduler = OcrFlowScheduler(contended_max_share_ratio=0.6)
    await scheduler.acquire("project:1", "full_page", capacity=1)

    per_wait = asyncio.create_task(
        scheduler.acquire("project:1", "per_image", capacity=1)
    )
    await asyncio.sleep(0)
    assert scheduler.get_stats()["flows"]["project:1"]["class_waiting"]["per_image"] == 1

    per_wait.cancel()
    await asyncio.gather(per_wait, return_exceptions=True)
    assert scheduler.get_stats()["flows"]["project:1"]["class_waiting"]["per_image"] == 0

    full_wait = asyncio.create_task(
        scheduler.acquire("project:1", "full_page", capacity=1)
    )
    await asyncio.sleep(0)
    await scheduler.release("project:1", "full_page")
    await asyncio.wait_for(full_wait, timeout=1)


@pytest.mark.asyncio
async def test_idle_flow_is_removed_from_stats() -> None:
    from app.core.parser.ocr_flow_scheduler import OcrFlowScheduler

    scheduler = OcrFlowScheduler(contended_max_share_ratio=0.6)
    await scheduler.acquire("project:1", "per_image", capacity=3)
    await scheduler.release("project:1", "per_image")

    assert scheduler.get_stats()["flows"] == {}
