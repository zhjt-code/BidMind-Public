"""任务观测装饰器 / 上下文管理器 (模块级零 service 依赖).

放在 `app.core.observability` 下而非 `app.services`, 是为了避免 task 层在模块 import
时触发 `app/services/__init__.py` 的 eager 加载, 进而引入 `project_service -> app.core.tasks`
的循环依赖 (已在 2026-04-23 生产环境捕获).

运行时才从 `app.services.task_observation_service` 惰性 import `record_task_observation`
(业务服务层), 既保留业务语义, 也保证模块加载顺序的安全.
"""

from __future__ import annotations

import functools
import inspect
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from loguru import logger

from app.models.task_observation import TaskFlow, TaskOutcome, TaskPhase

__all__ = [
    "TaskFlow",
    "TaskPhase",
    "TaskOutcome",
    "observed_task",
    "observe_task",
    "record_stage",
    "safe_duration_ms",
]


def safe_duration_ms(started_at: Optional[datetime], ended_at: Optional[datetime] = None) -> Optional[int]:
    if not started_at:
        return None
    end = ended_at or datetime.now(timezone.utc)
    try:
        return int((end - started_at).total_seconds() * 1000)
    except Exception:
        return None


async def _persist_observed_duration(**kwargs: Any) -> None:
    try:
        from app.services.task_observation_service import persist_observed_duration
    except Exception as e:
        logger.debug(f"observe_task: persist_observed_duration import 失败 (忽略): {e}")
        return
    await persist_observed_duration(**kwargs)


async def _record(
    *,
    task_id: str,
    flow: TaskFlow,
    task_type: str,
    phase: TaskPhase,
    project_id: Optional[int],
    stage: Optional[str] = None,
    outcome: Optional[TaskOutcome] = None,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
    duration_ms: Optional[int] = None,
    last_error: Optional[str] = None,
    labels: Optional[dict[str, Any]] = None,
) -> None:
    """运行时延迟导入 service 层, 打破模块级循环依赖."""
    try:
        from app.services.task_observation_service import record_task_observation
    except Exception as e:  # 极端情况下 service 层不可用, 直接静默
        logger.debug(f"observe_task: record_task_observation import 失败 (忽略): {e}")
        return
    await record_task_observation(
        task_id=task_id,
        flow=flow,
        task_type=task_type,
        phase=phase,
        project_id=project_id,
        stage=stage,
        outcome=outcome,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        last_error=last_error,
        labels=labels,
    )


def _default_task_id(flow: TaskFlow, task_type: str, project_id: Optional[int]) -> str:
    """与 _TaskObservationContext 保持一致的 task_id 约定, 使 STAGE 事件与 START/END 同链。"""
    if project_id is not None:
        return f"{flow.value}:{project_id}:{task_type}"
    return f"{flow.value}:{task_type}"


async def record_stage(
    *,
    flow: TaskFlow,
    task_type: str,
    project_id: Optional[int],
    stage: str,
    task_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    labels: Optional[dict[str, Any]] = None,
) -> None:
    """记录一次阶段转换观测 (phase=STAGE)。

    用于在长流程任务内部标记阶段边界(如文档处理 parse/vl/chunk/vectorize),
    使进行中的任务也能在项目时间线里看到“当前走到哪个阶段”。
    失败静默, 不阻塞主流程。
    """
    await _record(
        task_id=task_id or _default_task_id(flow, task_type, project_id),
        flow=flow,
        task_type=task_type,
        phase=TaskPhase.STAGE,
        project_id=project_id,
        stage=stage,
        started_at=datetime.now(timezone.utc),
        duration_ms=duration_ms,
        labels=labels,
    )


class _TaskObservationContext:
    """异步上下文管理器: 进入时写 start, 退出时写 end (success/failure)."""

    def __init__(
        self,
        *,
        flow: TaskFlow,
        task_type: str,
        project_id: Optional[int] = None,
        task_id: Optional[str] = None,
        labels: Optional[dict[str, Any]] = None,
    ) -> None:
        self.flow = flow
        self.task_type = task_type
        self.project_id = project_id
        self.labels = labels
        self.task_id = task_id or (
            f"{flow.value}:{project_id}:{task_type}" if project_id is not None else f"{flow.value}:{task_type}"
        )
        self.started_at: Optional[datetime] = None

    async def __aenter__(self) -> "_TaskObservationContext":
        self.started_at = datetime.now(timezone.utc)
        await _record(
            task_id=self.task_id,
            flow=self.flow,
            task_type=self.task_type,
            phase=TaskPhase.START,
            project_id=self.project_id,
            started_at=self.started_at,
            labels=self.labels,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        ended = datetime.now(timezone.utc)
        duration_ms = safe_duration_ms(self.started_at, ended)
        if exc_type is None:
            await _record(
                task_id=self.task_id,
                flow=self.flow,
                task_type=self.task_type,
                phase=TaskPhase.END,
                project_id=self.project_id,
                outcome=TaskOutcome.SUCCESS,
                started_at=self.started_at,
                ended_at=ended,
                duration_ms=duration_ms,
                labels=self.labels,
            )
            await _persist_observed_duration(
                task_id=self.task_id,
                flow=self.flow,
                task_type=self.task_type,
                project_id=self.project_id,
                started_at=self.started_at,
                ended_at=ended,
                duration_ms=duration_ms,
            )
        else:
            await _record(
                task_id=self.task_id,
                flow=self.flow,
                task_type=self.task_type,
                phase=TaskPhase.END,
                project_id=self.project_id,
                outcome=TaskOutcome.FAILURE,
                started_at=self.started_at,
                ended_at=ended,
                duration_ms=duration_ms,
                last_error=f"{exc_type.__name__}: {exc}",
                labels=self.labels,
            )
        return None  # 不吞异常


def observed_task(
    *,
    flow: TaskFlow,
    task_type: str,
    project_id: Optional[int] = None,
    task_id: Optional[str] = None,
    labels: Optional[dict[str, Any]] = None,
) -> _TaskObservationContext:
    return _TaskObservationContext(
        flow=flow, task_type=task_type, project_id=project_id, task_id=task_id, labels=labels,
    )


def observe_task(
    *,
    flow: TaskFlow,
    task_type: str,
    project_id_param: str = "project_id",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """异步函数装饰器: 进入时写 start, 退出时写 end, 自动从形参中提取 project_id.

    用法::

        @observe_task(flow=TaskFlow.TENDER, task_type="extract")
        async def run_extract_task(project_id: int) -> None: ...
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(fn):
            logger.warning(
                f"observe_task: {fn.__module__}.{fn.__qualname__} 非 async, 跳过装饰"
            )
            return fn

        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            project_id: Optional[int] = None
            try:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                raw = bound.arguments.get(project_id_param)
                if isinstance(raw, int):
                    project_id = raw
                elif isinstance(raw, str) and raw.isdigit():
                    project_id = int(raw)
            except Exception:
                project_id = None

            # 兜底注入日志上下文 project_id, 与 worker.py 外层 contextualize 形成双保险:
            # 直接调用 run_*_task() 的场景(如测试 / 未来新调用方)也能让日志带上 project_id。
            # loguru 的 contextualize 是栈式覆盖, 重复注入相同值无害。
            with logger.contextualize(project_id=project_id):
                async with observed_task(
                    flow=flow,
                    task_type=task_type,
                    project_id=project_id,
                ):
                    return await fn(*args, **kwargs)

        return wrapper

    return decorator
