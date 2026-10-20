"""项目里程碑装饰器 (阶段三, 与 observe_task 同思路, 模块级零 service 依赖)。

设计:
- 装饰 async run_*_task: 进入时不写, 成功写 ``{event_key_prefix}.completed``,
  异常写 ``{event_key_prefix}.failed`` 并放过异常
- 自动从函数参数提取 ``project_id`` (默认参数名 project_id)
- 失败静默, 不阻塞主流程

放在 ``app.core.observability`` 而非 ``app.services`` 的原因与 observe_task 一致:
避免 task 模块加载时触发 service 层 eager 初始化导致循环依赖。
"""

from __future__ import annotations

import functools
import inspect
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from loguru import logger


def _extract_project_id(sig: inspect.Signature, args: tuple, kwargs: dict, param: str) -> Optional[int]:
    try:
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        raw = bound.arguments.get(param)
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
    except Exception:
        return None
    return None


async def _safe_record(
    *,
    project_id: Optional[int],
    event_key: str,
    summary: Optional[str],
    task_type: Optional[str],
    occurred_at: Optional[datetime] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    """运行时延迟导入 milestone_service, 打破模块级循环依赖。"""
    if project_id is None:
        return
    try:
        from app.services.milestone_service import record_milestone
    except Exception as e:
        logger.debug(f"milestone: record_milestone import 失败 (忽略): {e}")
        return
    try:
        await record_milestone(
            project_id=project_id,
            event_key=event_key,
            summary=summary,
            task_type=task_type,
            occurred_at=occurred_at,
            payload=payload,
        )
    except Exception as e:
        logger.debug(f"milestone: record_milestone 调用失败 (忽略): {e}")


def milestone(
    *,
    event_key_prefix: str,
    task_type: Optional[str] = None,
    project_id_param: str = "project_id",
    summary_on_success: Optional[str] = None,
    summary_on_failure: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """async 装饰器: 任务结束时自动写一条里程碑事件。

    用法::

        @milestone(event_key_prefix="extract", task_type="extract")
        @observe_task(flow=TaskFlow.TENDER, task_type="extract")
        async def run_extract_task(project_id: int) -> None: ...

    成功时写 ``{event_key_prefix}.completed``, 异常时写 ``{event_key_prefix}.failed`` 并放过异常。

    Args:
        event_key_prefix: 事件键前缀, 例如 "extract" / "review" / "writing_compose"
        task_type: 任务类型, 写入 milestone.task_type, 与 system_error_logs.task_type 对齐
        project_id_param: 函数参数中 project_id 的名字, 默认 "project_id"
        summary_on_success: 成功摘要模板 (字符串), 缺省时使用通用模板
        summary_on_failure: 失败摘要模板, 缺省时填异常一句话
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(fn):
            logger.warning(
                f"milestone: {fn.__module__}.{fn.__qualname__} 非 async, 跳过装饰"
            )
            return fn

        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            project_id = _extract_project_id(sig, args, kwargs, project_id_param)
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                summary = summary_on_failure or f"{exc.__class__.__name__}: {exc}"
                await _safe_record(
                    project_id=project_id,
                    event_key=f"{event_key_prefix}.failed",
                    summary=summary[:500] if summary else None,
                    task_type=task_type or event_key_prefix,
                    occurred_at=datetime.now(timezone.utc),
                )
                raise
            await _safe_record(
                project_id=project_id,
                event_key=f"{event_key_prefix}.completed",
                summary=summary_on_success,
                task_type=task_type or event_key_prefix,
                occurred_at=datetime.now(timezone.utc),
            )
            return result

        return wrapper

    return decorator


__all__ = ["milestone"]
