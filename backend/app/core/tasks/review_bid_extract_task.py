"""投标审查 - 投标响应提取独立 step。

该 step 是 review_project_job 黑盒拆解的第一步, 供 orchestrator 后续释放。
"""

from __future__ import annotations

from loguru import logger

from app.core.database import async_session_factory


def get_review_bid_extract_task_name(project_id: int) -> str:
    """统一任务名格式。"""

    return f"review_bid_extract_{project_id}"


async def run_review_bid_extract_task(project_id: int) -> None:
    """仅执行投标响应提取并落库。"""

    from app.core.ai import release_project_binding
    from app.core.arq_runtime import ProjectCancelledError, is_project_cancelled, raise_if_project_cancelled
    from app.core.config import get_review_concurrency
    from app.core.extractor.bid_extractor import get_bid_extractor
    from app.core.tasks import update_heartbeat
    from app.core.tasks.cancellation import make_project_cancel_check
    from app.core.tasks.parallel_unit_progress import parallel_unit_heartbeat_extra

    task_name = get_review_bid_extract_task_name(project_id)
    update_heartbeat(task_name, stage="starting", progress="0%|初始化")

    if is_project_cancelled(project_id):
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return
    cancel_check = make_project_cancel_check(project_id)

    async with async_session_factory() as db:
        try:
            cancel_check()
            update_heartbeat(task_name, stage="bid_extract", progress="0/0|投标响应提取")
            extractor = get_bid_extractor(
                db,
                concurrency=get_review_concurrency(),
                project_id=project_id,
            )
            responses = await extractor.extract_for_project(
                project_id=project_id,
                save_to_db=True,
                skip_existing=True,
                heartbeat_callback=lambda done, total, inflight=0: update_heartbeat(
                    task_name,
                    stage="bid_extract",
                    progress=f"{done}/{total}",
                    extra=parallel_unit_heartbeat_extra(done, total, inflight),
                ),
                cancel_check=cancel_check,
            )
            cancel_check()
            await db.commit()
            update_heartbeat(
                task_name,
                stage="completed",
                progress=f"完成|{len(responses)}条投标响应",
            )
            try:
                from app.models.task_run import TaskRunStatus
                from app.services.flow_retry_service import mark_review_task_run_by_task_name

                await mark_review_task_run_by_task_name(
                    db,
                    project_id=project_id,
                    task_name=task_name,
                    status=TaskRunStatus.completed.value,
                    stage="completed",
                    last_error=None,
                )
            except Exception as mark_err:
                logger.debug(f"[投标响应提取step] TaskRun 完成态回写失败(忽略): {mark_err}")
            logger.info(
                f"[投标响应提取step] 完成: project_id={project_id}, responses={len(responses)}"
            )
        except ProjectCancelledError:
            await db.rollback()
            update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
            logger.info(f"[投标响应提取step] 项目已取消, 静默退出: project_id={project_id}")
            return
        except Exception as exc:  # noqa: BLE001
            if is_project_cancelled(project_id):
                await db.rollback()
                update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
                return
            await db.rollback()
            update_heartbeat(task_name, stage="failed", progress=f"失败|{str(exc)[:200]}")
            try:
                from app.models.task_run import TaskRunStatus
                from app.services.flow_retry_service import mark_review_task_run_by_task_name

                await mark_review_task_run_by_task_name(
                    db,
                    project_id=project_id,
                    task_name=task_name,
                    status=TaskRunStatus.failed.value,
                    stage="failed",
                    last_error=str(exc),
                )
            except Exception as mark_err:
                logger.debug(f"[投标响应提取step] TaskRun 失败态回写失败(忽略): {mark_err}")
            logger.error(f"[投标响应提取step] 失败: project_id={project_id}, err={exc}", exc_info=True)
            raise
        finally:
            await release_project_binding(project_id)
