"""后台任务项目取消契约工具。

职责: 为后台任务内部循环提供统一项目取消检查与 gather 取消异常透传工具。
不做: 不写业务状态、不访问具体业务模型、不替代 worker 入口调度。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.core.arq_runtime import ProjectCancelledError, raise_if_project_cancelled


def make_project_cancel_check(project_id: int) -> Callable[[], None]:
    """构造可传入内部循环的同步取消检查函数。"""

    def _check() -> None:
        raise_if_project_cancelled(project_id)

    return _check


def raise_if_cancelled_between_batches(project_id: int, stage: str = "") -> None:
    """批次边界检查项目取消；stage 仅供调用方保留语义。"""

    raise_if_project_cancelled(project_id)


def rethrow_project_cancelled(results: Iterable[Any]) -> None:
    """从 gather(return_exceptions=True) 结果中重新抛出项目取消异常。"""

    for result in results:
        if isinstance(result, ProjectCancelledError):
            raise result


def invoke_cancel_check(cancel_check: Callable[[], None] | None) -> None:
    """执行可选取消检查，减少调用点样板代码。"""

    if cancel_check is not None:
        cancel_check()


__all__ = [
    "invoke_cancel_check",
    "make_project_cancel_check",
    "raise_if_cancelled_between_batches",
    "rethrow_project_cancelled",
]
