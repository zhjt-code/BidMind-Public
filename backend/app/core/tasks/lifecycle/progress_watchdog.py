"""业务进展看门狗（Wave1-A）

职责: 以业务进展见证判定长任务是否仍在推进，无进展超阈值即主动终止
不做: 不写项目终态/计费、不重投、不访问 DB、不判定 ARQ 在途
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable
from typing import Any, TypeVar

from loguru import logger

T = TypeVar("T")

# extra 中仅用于保活、不代表业务推进的字段，不得进入进展指纹。
_NON_PROGRESS_EXTRA_KEYS: frozenset[str] = frozenset({"last_heartbeat", "keepalive"})


class TaskProgressStalledError(Exception):
    """任务业务进展停滞超过阈值。"""

    def __init__(self, task_name: str, no_progress_sec: float) -> None:
        self.task_name = task_name
        self.no_progress_sec = no_progress_sec
        super().__init__(
            f"任务无业务进展超时: task={task_name}, no_progress_sec={no_progress_sec:.1f}"
        )


def _build_progress_fingerprint(heartbeat: dict[str, Any] | None) -> str:
    """从心跳快照构造业务进展指纹。

    指纹 = stage + progress + 序列化 extra（排除 last_heartbeat / keepalive）。
    顶层 ``last_heartbeat`` 本身不参与指纹，避免纯保活刷新被误判为进展。
    """
    if not heartbeat:
        return ""

    stage = heartbeat.get("stage", "")
    progress = heartbeat.get("progress", "")
    extra = heartbeat.get("extra")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (json.JSONDecodeError, TypeError):
            extra = {"_raw": extra}
    if not isinstance(extra, dict):
        extra = {} if extra is None else {"_value": extra}

    filtered_extra = {
        key: value
        for key, value in extra.items()
        if key not in _NON_PROGRESS_EXTRA_KEYS
    }
    payload = {
        "stage": stage,
        "progress": progress,
        "extra": filtered_extra,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


async def await_with_progress_guard(
    awaitable: Awaitable[T],
    *,
    task_name: str,
    max_no_progress_sec: float,
    poll_interval: float = 30.0,
) -> T:
    """包裹长 awaitable：心跳业务指纹无变化超过阈值则取消并抛停滞错误。

    - 无心跳时首次视为 baseline（空指纹 + 刷新时间），不立刻判死
    - 读心跳失败只记 debug，不阻断轮询
    - 达 50% 阈值打一次 WARN
    - 外部取消/其他异常：取消内层 task 后原样重抛
    """
    from app.core.tasks import get_task_heartbeat

    inner = asyncio.ensure_future(awaitable)
    last_fingerprint: str | None = None
    last_progress_at = time.monotonic()
    warned_half = False

    async def _cancel_inner() -> None:
        if not inner.done():
            inner.cancel()
            await asyncio.gather(inner, return_exceptions=True)

    try:
        while True:
            if inner.done():
                return await inner

            try:
                heartbeat = get_task_heartbeat(task_name)
            except Exception as exc:
                logger.debug(
                    f"[progress_watchdog] 读心跳失败(忽略): task={task_name}, err={exc}"
                )
            else:
                fingerprint = _build_progress_fingerprint(heartbeat)
                if last_fingerprint is None:
                    # 首次（含无心跳）记为 baseline，不立刻判死
                    last_fingerprint = fingerprint
                    last_progress_at = time.monotonic()
                elif fingerprint != last_fingerprint:
                    last_fingerprint = fingerprint
                    last_progress_at = time.monotonic()

            stalled_for = time.monotonic() - last_progress_at
            if stalled_for >= max_no_progress_sec:
                await _cancel_inner()
                raise TaskProgressStalledError(
                    task_name=task_name,
                    no_progress_sec=stalled_for,
                )

            if (
                not warned_half
                and max_no_progress_sec > 0
                and stalled_for >= max_no_progress_sec * 0.5
            ):
                logger.warning(
                    f"[progress_watchdog] 任务长时间无业务进展: task={task_name}, "
                    f"no_progress={stalled_for:.0f}s / limit={max_no_progress_sec:.0f}s"
                )
                warned_half = True

            done, _pending = await asyncio.wait({inner}, timeout=poll_interval)
            if inner in done:
                return await inner
    except TaskProgressStalledError:
        raise
    except BaseException:
        await _cancel_inner()
        raise
