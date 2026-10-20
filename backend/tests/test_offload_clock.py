"""卸载耗时归因契约测试。

问题形态: 设备故障谓词不得使用调度器墙钟。
本文件锁三分归因与 grace 窗口, 不涉及具体设备。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.vectorizer.offload_clock import (
    TIMEOUT_DEVICE_STALL,
    TIMEOUT_QUEUED,
    TIMEOUT_SCHEDULER_DELAY,
    OffloadDeviceTimeout,
    OffloadObservation,
    observed_call,
    wait_for_offload,
)


def test_classify_timeout_queued_when_not_started() -> None:
    observation = OffloadObservation()
    assert observation.classify_timeout(0.05, time.perf_counter()) == TIMEOUT_QUEUED
    assert observation.device_elapsed_sec is None


def test_classify_timeout_scheduler_delay_when_device_already_finished() -> None:
    """超时回调晚于设备完成时, 只要设备耗时仍在预算内, 就不是设备故障。"""
    now = time.perf_counter()
    observation = OffloadObservation(started_at=now - 0.20, finished_at=now - 0.18)
    assert observation.classify_timeout(0.05, now) == TIMEOUT_SCHEDULER_DELAY
    assert observation.device_elapsed_sec is not None
    assert observation.device_elapsed_sec < 0.05
    now = time.perf_counter()
    observation = OffloadObservation(started_at=now - 0.01, finished_at=None)
    assert observation.classify_timeout(0.05, now) == TIMEOUT_SCHEDULER_DELAY


def test_classify_timeout_device_stall_when_budget_spent() -> None:
    now = time.perf_counter()
    observation = OffloadObservation(started_at=now - 0.20, finished_at=None)
    assert observation.classify_timeout(0.05, now) == TIMEOUT_DEVICE_STALL


def test_observed_call_records_device_elapsed_not_caller_delay() -> None:
    observation = OffloadObservation()

    def work() -> str:
        time.sleep(0.02)
        return "done"

    assert observed_call(work, observation)() == "done"
    elapsed = observation.device_elapsed_sec
    assert elapsed is not None
    assert 0.01 <= elapsed < 0.15


@pytest.mark.asyncio
async def test_wait_for_offload_grace_when_still_queued() -> None:
    """执行器被占满时墙钟先超时, 不得当成设备故障; grace 内完成即成功。"""
    loop = asyncio.get_running_loop()
    gate = threading.Event()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    pool.submit(lambda: gate.wait(1.0))
    observation = OffloadObservation()

    def work() -> str:
        return "landed"

    future = loop.run_in_executor(pool, observed_call(work, observation))

    async def release() -> None:
        await asyncio.sleep(0.03)
        gate.set()

    releaser = asyncio.create_task(release())
    try:
        result = await wait_for_offload(future, 0.02, observation)
    finally:
        gate.set()
        await releaser
        pool.shutdown(wait=False)

    assert result == "landed"
    assert observation.device_elapsed_sec is not None
    assert observation.device_elapsed_sec < 0.05


@pytest.mark.asyncio
async def test_wait_for_offload_device_stall_when_budget_spent() -> None:
    """设备内耗时越过预算时, grace 不得把真故障放行。"""
    loop = asyncio.get_running_loop()
    observation = OffloadObservation()

    def work() -> str:
        time.sleep(0.5)
        return "late"

    future = loop.run_in_executor(None, observed_call(work, observation))
    with pytest.raises(OffloadDeviceTimeout) as caught:
        await wait_for_offload(future, 0.05, observation)
    assert caught.value.cause == TIMEOUT_DEVICE_STALL
