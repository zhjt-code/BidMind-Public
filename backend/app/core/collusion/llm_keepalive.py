"""串标长 LLM 调用心跳保活。

职责: 在串标长时间 LLM 等待期间周期性刷新业务心跳。
不做: 不决定 LLM 重试策略、不修改分析结果、不访问数据库。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def await_with_collusion_keepalive(
    awaitable: Awaitable[T],
    *,
    heartbeat: Callable[[str, str, dict | None], None],
    stage: str,
    progress: str,
    interval: float = 30.0,
    extra: dict | None = None,
) -> T:
    """等待长耗时协程时周期性刷新串标业务心跳。"""
    task = asyncio.create_task(awaitable)
    try:
        while True:
            heartbeat(stage, progress, {**(extra or {}), "keepalive": True})
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=interval)
            except asyncio.TimeoutError:
                continue
    except BaseException:
        if not task.done():
            task.cancel()
        raise
