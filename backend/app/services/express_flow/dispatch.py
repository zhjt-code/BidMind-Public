"""
职责: 在阶段任务成功后入队 express 推进入口。
不做: 不判断业务就绪态、不执行确认或启动。
"""

from __future__ import annotations

from loguru import logger


def get_express_advance_task_name(project_id: int) -> str:
    return f"express_advance_{project_id}"


async def dispatch_express_advance(project_id: int) -> None:
    from app.core.tasks import dispatch_background_job
    from app.core.tasks.express_advance_task import run_express_advance_task

    task_name = get_express_advance_task_name(project_id)
    try:
        await dispatch_background_job(
            task_name=task_name,
            arq_function="express_advance_job",
            fallback_factory=lambda: run_express_advance_task(project_id),
            task_type="express_advance",
            project_id=project_id,
            arq_args=(project_id,),
        )
    except Exception as exc:
        logger.warning(
            "dispatch express_advance 失败(忽略，不回滚已完成阶段): project_id={}, err={}",
            project_id,
            exc,
        )


async def queue_express_advance_if_needed(run_mode: str | None, project_id: int) -> None:
    if run_mode != "express":
        return
    await dispatch_express_advance(project_id)
