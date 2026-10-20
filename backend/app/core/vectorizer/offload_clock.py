"""
职责: 为卸载到线程或进程执行器的计算提供设备内耗时观测与超时归因
不做: 不选择设备、不做降级决策、不管理执行器、不读取任何设备指标
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from loguru import logger

TIMEOUT_QUEUED = "queued"
TIMEOUT_SCHEDULER_DELAY = "scheduler_delay"
TIMEOUT_DEVICE_STALL = "device_stall"

T = TypeVar("T")


class OffloadDeviceTimeout(TimeoutError):
    """设备内耗时已用尽预算。调用方才应把这次等待当成设备故障。"""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


@dataclass
class OffloadObservation:
    """单次卸载计算的耗时观测。started_at 为 None 表示仍在执行器队列未开始。"""

    started_at: float | None = None
    finished_at: float | None = None

    @property
    def device_elapsed_sec(self) -> float | None:
        """执行器内实际耗时。未开始或未结束时返回 None，禁止用墙钟冒充。"""
        if self.started_at is None or self.finished_at is None:
            return None
        return max(0.0, self.finished_at - self.started_at)

    def classify_timeout(self, timeout_sec: float, now: float) -> str:
        """墙钟超时的归因三分: queued / scheduler_delay / device_stall。

        设备预算按执行器内起止时间计算。设备已在预算内结束, 或尚未跑满预算时,
        墙钟先到只说明调度器醒得晚, 不得判成设备故障。
        """
        if self.started_at is None:
            return TIMEOUT_QUEUED
        if (
            self.finished_at is not None
            and (self.finished_at - self.started_at) < timeout_sec
        ):
            return TIMEOUT_SCHEDULER_DELAY
        if now - self.started_at < timeout_sec:
            return TIMEOUT_SCHEDULER_DELAY
        return TIMEOUT_DEVICE_STALL


def observed_call(
    fn: Callable[..., T],
    observation: OffloadObservation,
) -> Callable[..., T]:
    """包一层在执行器线程内写入起止时间的调用。不改变原函数的参数与返回值。"""

    def wrapper(*args: Any, **kwargs: Any) -> T:
        observation.started_at = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            observation.finished_at = time.perf_counter()

    return wrapper


async def wait_for_offload(
    future: Awaitable[T],
    timeout_sec: float,
    observation: OffloadObservation,
) -> T:
    """等待已提交的卸载计算。

    职责: 只在设备预算用尽时抛 OffloadDeviceTimeout
    不做: 不取消 future、不决定降级

    墙钟先超时但设备尚未跑满预算（queued / scheduler_delay）时，
    再等一个等额窗口让结果落地；该窗口仍未完成才升级为设备故障。
    """
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_sec)
    except asyncio.TimeoutError:
        cause = observation.classify_timeout(timeout_sec, time.perf_counter())
        if cause == TIMEOUT_DEVICE_STALL:
            raise OffloadDeviceTimeout(cause) from None
        logger.warning(
            f"[offload_clock] 墙钟超时归因={cause}, 设备预算未用尽, "
            f"再等 {timeout_sec:.3f}s"
        )
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_sec)
        except asyncio.TimeoutError:
            raise OffloadDeviceTimeout(TIMEOUT_DEVICE_STALL) from None
