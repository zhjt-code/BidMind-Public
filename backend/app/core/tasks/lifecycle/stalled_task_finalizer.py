"""无进展终止的项目失败态收敛（Wave1-A）

职责: 把「无进展终止」收敛为可诊断的项目失败态
不做: 不重投任务、不改恢复策略
"""

from __future__ import annotations

from loguru import logger

# ARQ function_name -> RetryService writing node_key；非写标节点不入表。
_WRITING_NODE_BY_FUNCTION: dict[str, str] = {
    "writing_extract_job": "writing_extract",
    "writing_outline_job": "writing_outline",
    "writing_match_job": "writing_match",
    "writing_compose_job": "writing_compose",
    "writing_compose_single_job": "writing_compose_single",
}


def _parse_compose_single_node_id(task_name: str, project_id: int) -> int | None:
    """从 writing_compose_single_{project_id}_{node_id} 解析 node_id。"""
    prefix = f"writing_compose_single_{project_id}_"
    if not task_name.startswith(prefix):
        return None
    suffix = task_name[len(prefix) :]
    try:
        return int(suffix)
    except ValueError:
        return None


async def finalize_stalled_task(
    project_id: int,
    task_name: str,
    no_progress_sec: float,
    *,
    function_name: str | None = None,
) -> None:
    """将无进展终止收敛为 FAILED，并尽力同步心跳 / RetryService / 计费。

    全程 best-effort：任意步骤失败只记 warning，不向外抛，
    以便调用方继续 re-raise ``TaskProgressStalledError``。
    """
    minutes = max(1, round(no_progress_sec / 60))
    error_message = f"任务连续 {minutes} 分钟无业务进展，已终止，可重新发起"

    try:
        from app.core.database import AsyncSessionLocal
        from app.core.tasks import update_heartbeat
        from app.models.project import Project, ProjectStatus

        async with AsyncSessionLocal() as db:
            project = await db.get(Project, project_id)
            if project is not None:
                project.status = ProjectStatus.FAILED
                project.error_message = error_message
                await db.commit()

                try:
                    from app.billing.services.billing_guard import BillingGuard

                    await BillingGuard.on_failed(db, project)
                    await db.commit()
                except Exception as billing_err:
                    logger.warning(
                        f"[stalled_task_finalizer] BillingGuard.on_failed 失败(忽略): "
                        f"project={project_id}, err={billing_err}"
                    )

            try:
                update_heartbeat(
                    task_name,
                    stage="failed",
                    progress=error_message,
                )
            except Exception as hb_err:
                logger.warning(
                    f"[stalled_task_finalizer] 更新失败心跳异常(忽略): "
                    f"task={task_name}, err={hb_err}"
                )

            node_key = _WRITING_NODE_BY_FUNCTION.get(function_name or "")
            if node_key:
                target_id: int | None = None
                if node_key == "writing_compose_single":
                    target_id = _parse_compose_single_node_id(task_name, project_id)
                    if target_id is None:
                        logger.warning(
                            f"[stalled_task_finalizer] 无法解析 compose_single node_id，"
                            f"跳过 RetryService: task={task_name}"
                        )
                        return
                try:
                    from app.services.retry_service import RetryService

                    await RetryService(db).mark_task_failed(
                        project_id,
                        node_key=node_key,
                        target_id=target_id,
                        stage="failed",
                        error=error_message,
                    )
                except Exception as retry_err:
                    logger.warning(
                        f"[stalled_task_finalizer] mark_task_failed 失败(忽略): "
                        f"project={project_id}, node={node_key}, err={retry_err}"
                    )
    except Exception as exc:
        logger.warning(
            f"[stalled_task_finalizer] 收敛失败态异常(忽略): "
            f"project={project_id}, task={task_name}, err={exc}"
        )
