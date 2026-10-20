"""文档处理失败/取消收尾

职责:
- handle_cancel: 项目级取消(ProjectCancelledError)路径; 不写 FAILED, 不写 VectorJobState,
 避免外键失败。
- handle_failure: 通用异常路径; 二次取消检查(异常发生时若已取消按 cancelled 收尾),
 写 ProjectStatus.FAILED + VectorJobState.FAILED + RetryService.mark_task_failed。

外层主编排器只负责 try/except 路由, 失败收尾的 DB 操作全部内聚此模块。
"""

from __future__ import annotations

from typing import Callable, Optional

from loguru import logger


async def handle_cancel(
    *,
    doc_project_id: int,
    document_id: int,
    heartbeat: Callable[..., None],
) -> None:
    """ProjectCancelledError 路径: 仅刷一次 cancelled 心跳, 不动 DB。"""
    logger.info(
        f"[文档处理] 任务已因项目取消而中止: project_id={doc_project_id}, document_id={document_id}"
    )
    heartbeat("cancelled", "任务已取消")


async def handle_interrupt(
    *,
    project_id: int,
    document_id: int,
    task_name: str,
    queue_mode: str,
    worker_id: Optional[str],
    heartbeat: Callable[..., None],
) -> None:
    """任务被取消(asyncio.CancelledError)路径: worker 优雅关闭/超时/abort 等。: 取消可能发生在 RUNNING 落库之前, 若不处理则状态机停在 QUEUED 且无日志,
 成为永不自愈的幽灵任务。此处不写 FAILED(取消通常可恢复, 不应标记项目终态失败),
 改写 VectorJobState.LOST -> reconciler 必恢复; 全程 best-effort, 不吞 CancelledError
 (由调用方 re-raise 保持 ARQ 取消语义)。

 守卫: 若取消源于项目删除, 不写任何状态, 避免外键失效。
 """
    from app.core.arq_runtime import is_project_cancelled
    from app.core.config import settings
    from app.core.tasks import process_task as ptm
    from app.core.vectorizer.job_state import upsert_vector_job_state
    from app.models.vector_job_state import VectorJobStatus

    if is_project_cancelled(project_id):
        logger.info(
            f"[文档处理] 任务取消且项目已删除/取消, 不写状态: "
            f"project_id={project_id}, document_id={document_id}"
        )
        return

    logger.warning(
        f"[文档处理] 任务被中断(取消), 标记为可恢复 LOST 交由 reconciler 重投: "
        f"project_id={project_id}, document_id={document_id}, task_name={task_name}"
    )
    try:
        heartbeat("lost", "任务被中断, 等待自动恢复")
    except Exception:
        pass
    try:
        async with ptm.async_session_factory() as db:
            await upsert_vector_job_state(
                db,
                project_id=project_id,
                document_id=document_id,
                task_name=task_name,
                status=VectorJobStatus.LOST,
                queue_mode=queue_mode,
                stage="lost",
                progress="任务被中断, 等待自动恢复",
                last_error="interrupted(cancelled before completion)",
                chunk_fingerprint_version=str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
                worker_id=worker_id,
            )
            await db.commit()
    except Exception as inner_e:  # noqa: BLE001
        # 取消窗口内 DB 写入可能再次被取消/中断, 失败也无妨:
        # 状态仍停在 QUEUED, 由 reconciler 的"幽灵 QUEUED"分支兜底恢复。
        logger.warning(f"[文档处理] 中断态落库失败(忽略, 由 reconciler 兜底): {inner_e}")


async def handle_failure(
    *,
    error: BaseException,
    doc_project_id: int,
    document_id: int,
    task_name: str,
    queue_mode: str,
    worker_id: Optional[str],
    heartbeat: Callable[..., None],
) -> None:
    """通用异常路径.

 防御: 若异常发生时项目已被取消, 走 cancelled 路径, 不写 FAILED。
 否则: ProjectStatus.FAILED + VectorJobState.FAILED + RetryService.mark_task_failed。

 内层 except 兜底: 失败状态更新本身若再失败, 仅记录 error log, 不再向上抛。
 """
    from app.core.arq_runtime import is_project_cancelled
    from app.core.config import settings
    from app.core.tasks import process_task as ptm
    from app.core.vectorizer.job_state import upsert_vector_job_state
    from app.models.project import ProjectStatus
    from app.models.vector_job_state import VectorJobStatus

    # 二次检查: 若异常发生时项目已被取消, 按取消路径收尾, 避免写 FAILED 触发外键
    if is_project_cancelled(doc_project_id):
        logger.info(
            f"[文档处理] 异常发生时项目已被取消, 按取消路径收尾: project_id={doc_project_id}, "
            f"document_id={document_id}, err={str(error)[:200]}"
        )
        heartbeat("cancelled", "任务已取消")
        return

    logger.error(f"[文档处理] 失败: {error}", exc_info=True)
    heartbeat("failed", str(error)[:200])

    try:
        async with ptm.async_session_factory() as err_db:
            project = await ptm._get_project(err_db, doc_project_id)
            if project:
                project.status = ProjectStatus.FAILED
                project.error_message = str(error)[:1000]
            await upsert_vector_job_state(
                err_db,
                project_id=doc_project_id,
                document_id=document_id,
                task_name=task_name,
                status=VectorJobStatus.FAILED,
                queue_mode=queue_mode,
                stage="failed",
                progress=str(error)[:200],
                last_error=str(error)[:1000],
                chunk_fingerprint_version=str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
                worker_id=worker_id,
                bump_finished_at=True,
            )
            try:
                from app.models.task_run import TaskRunStatus
                from app.services.flow_retry_service import mark_review_task_run_by_task_name
                from app.services.retry_service import RetryService

                await RetryService(err_db).mark_task_failed(
                    doc_project_id,
                    node_key=RetryService.NODE_WRITING_TENDER_PROCESS,
                    target_id=document_id,
                    stage="failed",
                    error=str(error),
                )
                await mark_review_task_run_by_task_name(
                    err_db,
                    project_id=doc_project_id,
                    task_name=task_name,
                    status=TaskRunStatus.failed.value,
                    stage="failed",
                    last_error=str(error),
                )
            except Exception as retry_err:
                logger.debug(f"[文档处理] 更新显式重试失败态失败(忽略): {retry_err}")
            await err_db.commit()
    except Exception as inner_e:
        logger.error(f"[文档处理] 更新失败状态时出错: {inner_e}")
