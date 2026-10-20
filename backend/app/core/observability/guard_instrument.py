"""Guard 埋点辅助工具（B0.5 用）。

两种用法：

1. 上下文管理器 `guard_observe` —— 适合类方法（可以包裹函数体）::

    def _apply_relevance_guard(extracted, pair):
        with guard_observe("_apply_relevance_guard", "investigate"):
            ...原有逻辑...

2. 装饰器 `observe_guard` —— 适合模块级长函数（不需要重新缩进）::

    @observe_guard("D1PersonnelClaimGate", "collusion")
    def evaluate(self, ctx):
        ...原有逻辑...
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator

from . import OBSERVABILITY_ENABLED
from .metrics import guard_invocation_total, guard_latency_seconds
from .trace import guard_run_trace


@contextmanager
def guard_observe(
    guard_name: str,
    flow: str,
    *,
    target_id: Any = None,
    input_hint: str = "",
) -> Generator[dict[str, Any], None, None]:
    """Guard 埋点上下文管理器。

    Parameters
    ----------
    guard_name : str
        Guard 函数名（如 ``_apply_relevance_guard``）。
    flow : str
        所属流程：``investigate`` / ``collusion`` / ``writer``。
    target_id : Any, optional
        业务对象标识（requirement_id / pair_id / node_id）。
    input_hint : str, optional
        输入要点字符串，用于计算 input_hash（便于 A/B 复现）。

    Yields
    ------
    dict
        一个可写字典，guard 函数内部可设置 ``verdict_hint``
        （``"pass"`` / ``"warn"`` / ``"would_reject"``）来细化观测结果。
        若未设置，默认为 ``"pass"``。

    Notes
    -----
    - 不改变 guard 的返回值或副作用
    - verdict_hint 仅写入 observability，不暴露到业务对象
    - OBSERVABILITY_ENABLED=False 时仍 yield 但跳过所有 I/O
    """
    ctx: dict[str, Any] = {"verdict_hint": "pass"}

    if not OBSERVABILITY_ENABLED:
        yield ctx
        return

    input_hash = hashlib.sha256(input_hint.encode()).hexdigest()[:8] if input_hint else ""
    t0 = time.perf_counter()

    try:
        yield ctx
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        verdict = ctx.get("verdict_hint", "pass")

        guard_invocation_total.labels(
            guard_name=guard_name,
            flow=flow,
            verdict=verdict,
        ).inc()

        guard_latency_seconds.labels(
            guard_name=guard_name,
            flow=flow,
        ).observe(elapsed_ms / 1000)

        guard_run_trace(
            guard_name=guard_name,
            flow=flow,
            target_id=target_id,
            applied=verdict != "pass",
            verdict_hint=verdict,
            latency_ms=elapsed_ms,
            input_hash=input_hash,
        )


def observe_guard(guard_name: str, flow: str) -> Callable:
    """Guard 埋点装饰器（适合模块级函数，不需要重新缩进函数体）。

    支持同步和异步函数。verdict_hint 固定为 "pass"（装饰器模式下
    无法从函数内部细粒度设置，但对指标计数和延迟追踪已足够）。
    """

    def decorator(fn: Callable) -> Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with guard_observe(guard_name, flow):
                    return await fn(*args, **kwargs)
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with guard_observe(guard_name, flow):
                return fn(*args, **kwargs)
        return sync_wrapper

    return decorator
