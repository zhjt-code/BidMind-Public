# -*- coding: utf-8 -*-
"""长阶段任务心跳保活。

职责: 在没有进度回调的长耗时 await 期间, 按固定间隔刷新任务业务心跳,
 使"心跳新鲜度"如实反映任务是否仍在推进。
不做: 不决定重试/超时/取消策略、不修改被等待协程的结果或异常、不访问
 数据库/LLM、不判定任务是否完成(完成态由业务终态背书, 见
 requirement_service._EXTRACT_SETTLED_STATUSES)。

与 `app.core.collusion.llm_keepalive` 的关系: 两者保活语义相同, 但心跳写入
契约不同——串标版接收 `_hb(stage, progress, extra)` 闭包, 本模块直接对接
`update_heartbeat(name, stage=, progress=)`。故不共用实现, 也不跨层互相 import
(tasks 层不得反向依赖 collusion 领域模块)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Optional, TypeVar, Union

from loguru import logger

T = TypeVar("T")

# 取值依据: 心跳判活阈值 HEARTBEAT_TIMEOUT=600s 有 20 倍余量；
# 审查状态接口覆盖为 ARQ_JOB_HEARTBEAT_TIMEOUT=180s，仍有 6 倍余量。
# 间隔远小于单次云端 LLM 调用耗时区间(实测 7~55s)，不显著增加 Redis 写入。
DEFAULT_KEEPALIVE_INTERVAL_SEC = 30.0


def current_heartbeat_stage_progress(task_name: str) -> tuple[str, str]:
    """读取当前心跳 stage/progress，供保活沿用已有文案。

    职责: 从任务心跳快照取出展示用 stage 与 progress
    不做: 不写心跳、不判活、不访问业务库
    """
    from app.core.tasks import get_task_heartbeat

    snapshot = get_task_heartbeat(task_name) or {}
    return str(snapshot.get("stage") or ""), str(snapshot.get("progress") or "")


async def await_with_task_keepalive(
    awaitable: Awaitable[T],
    *,
    task_name: str,
    stage: Union[str, Callable[[], str]],
    progress: Union[str, Callable[[], str]],
    interval: float = DEFAULT_KEEPALIVE_INTERVAL_SEC,
    extra: Optional[dict[str, Any]] = None,
    heartbeat: Optional[Callable[..., None]] = None,
) -> T:
    """等待长耗时协程, 期间周期刷新心跳; 协程的返回值与异常均原样透传。

 背景: 招标要求提取的评分项阶段只在入口刷一次心跳, doc1923 实测静默
 24.5 分钟, 心跳龄越过 600s 后 `is_task_active` 翻假, 状态接口据此对前端宣告
 "提取完成"而评分项实为 0 条。保活让长阶段内心跳持续新鲜, 从源头消除该误判。

 取消安全: 外部取消/异常时先取消内部 task 再向上抛, 不留孤儿协程。
 刷新失败不阻断业务: 心跳写入异常只记 debug 日志, 绝不影响被等待协程。
 """
    if heartbeat is None:
        from app.core.tasks import update_heartbeat

        heartbeat = update_heartbeat

    task = asyncio.create_task(awaitable)
    try:
        while True:
            _refresh(heartbeat, task_name, stage, progress, extra)
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=interval)
            except asyncio.TimeoutError:
                continue
    except BaseException:
        if not task.done():
            task.cancel()
        raise


def _resolve_progress(progress: Union[str, Callable[[], str]]) -> str:
    """每次刷新再取值, 使保活跟上业务回调刚写下的最新文案。"""
    return progress() if callable(progress) else progress


def _refresh(
    heartbeat: Callable[..., None],
    task_name: str,
    stage: Union[str, Callable[[], str]],
    progress: Union[str, Callable[[], str]],
    extra: Optional[dict[str, Any]],
) -> None:
    """刷新一次心跳; 任何写入异常都被吞掉, 保活绝不能成为业务失败来源。"""
    payload = {**(extra or {}), "keepalive": True}
    try:
        heartbeat(
            task_name,
            stage=_resolve_progress(stage),
            progress=_resolve_progress(progress),
            extra=payload,
        )
    except Exception as exc:  # noqa: BLE001 - 保活失败不得阻断被等待协程
        logger.debug(f"心跳保活刷新失败(忽略): task={task_name}, err={exc}")


__all__ = [
    "DEFAULT_KEEPALIVE_INTERVAL_SEC",
    "await_with_task_keepalive",
    "current_heartbeat_stage_progress",
]
