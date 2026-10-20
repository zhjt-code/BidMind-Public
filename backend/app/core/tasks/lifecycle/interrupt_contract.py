"""任务中断痕迹记录（轻量版）

职责: 在 asyncio.CancelledError 发生时留下可诊断的中断痕迹
不做: 不写 Project 终态、不释放计费预占、不取消远端 job、不吞异常

为什么不写 FAILED:
 沿用 `tasks/process/failure_handler.handle_interrupt` 的既有哲学——取消通常
 可恢复（worker 优雅关闭、滚动重启都会产生 CancelledError），标记项目终态失败
 会迫使用户手动重试。项目滞留由 liveness reaper 每分钟兜底，计费预占由
 hold_sweeper 每小时兜底并在超时后强制释放，故本模块只补可观测性这一层缺口。

为什么调用方必须 re-raise:
 ARQ 依赖 CancelledError 向上传播来完成 job 的取消语义。调用方捕获后必须
 `raise`，否则 worker 会把被取消的 job 当作正常完成。

为什么 except Exception 抓不到它:
 asyncio.CancelledError 在 Python 3.8+ 继承自 BaseException 而非 Exception，
 这正是 review/collusion/writing 主 task 看似有兜底 except 却仍静默滞留的根因。
"""

from __future__ import annotations

from loguru import logger


async def record_task_interrupted(
    *,
    project_id: int,
    task_name: str,
    task_label: str,
) -> None:
    """写入一条 interrupted 心跳，标明任务被中断而非静默消失。

    全程 best-effort：取消窗口内任何写入都可能再次被中断，失败一律忽略，
    因为兜底恢复链并不依赖本记录。
    """
    try:
        from app.core.arq_runtime import is_project_cancelled

        # 项目已删除/取消时不写任何状态，避免外键失效（沿用 守卫）
        if is_project_cancelled(project_id):
            logger.info(
                f"[{task_label}] 任务取消且项目已删除，不写中断痕迹: project_id={project_id}"
            )
            return
    except Exception:
        pass

    logger.warning(
        f"[{task_label}] 任务被中断(取消): project_id={project_id}, task={task_name}; "
        f"未标记项目失败，等待自动恢复或人工重试"
    )
    try:
        from app.core.tasks import update_heartbeat

        update_heartbeat(
            task_name,
            stage="interrupted",
            progress="任务被中断，等待自动恢复或人工重试",
            extra={"interrupted": True},
        )
    except Exception as exc:
        logger.debug(f"[{task_label}] 中断心跳写入失败(忽略): {exc}")
