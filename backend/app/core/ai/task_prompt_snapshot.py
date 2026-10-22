"""Task-level Prompt snapshot helpers.

职责: 为后台任务绑定并释放指定 flow 的 Prompt 版本快照。
不做: 不解析业务参数、不执行任何 LLM 调用。
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from app.core.ai.prompt_registry import current_snapshot, install_task_snapshot

F = TypeVar("F", bound=Callable[..., Any])


def with_task_prompt_snapshot(flow: str) -> Callable[[F], F]:
    """Decorate an async task so all registry calls use a task snapshot."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            token = install_task_snapshot(flow)
            try:
                return await func(*args, **kwargs)
            finally:
                if token is not None:
                    current_snapshot.reset(token)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["with_task_prompt_snapshot"]
