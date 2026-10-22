"""标书编写 -- 单章节重新生成后台任务

触发条件: 用户在内容编辑页点击"重新生成章节"
流程: 加载章节 -> 调用 SectionComposer.compose_single -> 写入草稿 -> 使渲染产物失效
任务名: writing_compose_single_{project_id}_{node_id}
心跳阶段(stage): queued -> starting -> composing -> completed / failed / cancelled

该任务被 ARQ worker 与 asyncio 回退路径共享,
严格复用 ``SectionComposer.compose_single`` 以避免再起旁路业务。
"""

import asyncio

from loguru import logger

from app.core.ai.task_prompt_snapshot import with_task_prompt_snapshot
from app.core.database import async_session_factory


def get_writing_compose_single_task_name(project_id: int, node_id: int) -> str:
    """统一任务名格式, 作为心跳键."""
    return f"writing_compose_single_{project_id}_{node_id}"


@with_task_prompt_snapshot("writing")
async def run_writing_compose_single_task(
    project_id: int,
    node_id: int,
    explicit_retry: bool = False,
) -> None:
    """执行单章节重新生成任务.

    Args:
        project_id: 项目 ID.
        node_id: 目录节点 ID.
    """
    from app.core.arq_runtime import (
        ProjectCancelledError,
        is_project_cancelled,
        raise_if_project_cancelled,
    )
    from app.core.tasks import update_heartbeat

    task_name = get_writing_compose_single_task_name(project_id, node_id)
    update_heartbeat(task_name, stage="starting", progress="0%|初始化")

    if is_project_cancelled(project_id):
        logger.info(
            f"[单章生成] 项目已取消/删除, 跳过执行: project_id={project_id}, node_id={node_id}"
        )
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return

    async with async_session_factory() as db:
        try:
            from app.services.retry_service import RetryService

            await RetryService(db).mark_task_running(
                project_id,
                node_key=RetryService.NODE_WRITING_COMPOSE_SINGLE,
                target_id=node_id,
                stage="starting",
            )
            from app.models.writing_outline_node import WritingOutlineNode

            node = await db.get(WritingOutlineNode, node_id)
            if not node or node.project_id != project_id:
                msg = f"节点不存在: node_id={node_id}"
                logger.error(f"[单章生成] {msg}")
                update_heartbeat(task_name, stage="failed", progress=f"失败|{msg}")
                return

            raise_if_project_cancelled(project_id)
            update_heartbeat(task_name, stage="composing", progress="10%|生成章节内容")

            from app.core.writer.section_composer import get_section_composer

            composer = get_section_composer(db=db, project_id=project_id)
            draft = await composer.compose_single(node, project_id)
            raise_if_project_cancelled(project_id)

            from app.services.writing_service import WritingService

            svc = WritingService(db)
            await svc._invalidate_render_artifact(project_id)
            await db.commit()

            update_heartbeat(
                task_name,
                stage="completed",
                progress="100%|生成完成",
                extra={"draft_id": draft.id, "node_id": node_id},
            )
            await RetryService(db).mark_task_completed(
                project_id,
                node_key=RetryService.NODE_WRITING_COMPOSE_SINGLE,
                target_id=node_id,
            )
            # 单章重生成完成后立即结算预占，不再等待小时级 hold_sweeper
            try:
                from app.billing.services.billing_guard import BillingGuard
                from app.models.project import Project

                billing_project = await db.get(Project, project_id)
                if billing_project is not None:
                    await BillingGuard.on_completed(db, billing_project)
                    await db.commit()
            except Exception as billing_err:
                logger.warning(
                    f"[单章生成] 计费结算异常(不影响任务态): project_id={project_id}, err={billing_err}"
                )
            logger.info(
                f"[单章生成] 完成: project_id={project_id}, node_id={node_id}, draft_id={draft.id}"
            )

        except ProjectCancelledError:
            logger.info(
                f"[单章生成] 项目已取消, 静默退出: project_id={project_id}, node_id={node_id}"
            )
            update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
            try:
                await db.rollback()
            except Exception:
                pass

        except asyncio.CancelledError:
            # CancelledError 继承 BaseException，except Exception 抓不到。
            from app.core.tasks.lifecycle.interrupt_contract import record_task_interrupted

            try:
                await db.rollback()
            except Exception:
                pass
            await record_task_interrupted(
                project_id=project_id,
                task_name=task_name,
                task_label="标书编写单章",
            )
            raise

        except Exception as e:
            logger.error(
                f"[单章生成] 失败: project_id={project_id}, node_id={node_id}, err={e}",
                exc_info=True,
            )
            update_heartbeat(
                task_name,
                stage="failed",
                progress=f"失败|{str(e)[:200]}",
            )
            try:
                await db.rollback()
                from app.services.retry_service import RetryService

                await RetryService(db).mark_task_failed(
                    project_id,
                    node_key=RetryService.NODE_WRITING_COMPOSE_SINGLE,
                    target_id=node_id,
                    stage="failed",
                    error=str(e),
                )
            except Exception:
                pass
            # 仅项目已完成(结算后重生成)时释放预占；
            # 中途流程复用的 hold 交由后续阶段门禁/清扫器处理，避免 baseline 重置漏计
            try:
                from app.billing.services.billing_guard import BillingGuard
                from app.models.project import Project, ProjectStatus

                billing_project = await db.get(Project, project_id)
                if (
                    billing_project is not None
                    and billing_project.status == ProjectStatus.COMPLETED
                ):
                    await BillingGuard.on_failed(db, billing_project)
                    await db.commit()
            except Exception:
                pass
