"""标书编写 -- 目录树生成后台任务

触发条件: 用户在目录编辑页触发生成
流程: 清理旧目录 -> LLM生成 -> 写入DB
进度阶段: starting -> generating -> completed / failed
项目状态: OUTLINING
"""

import asyncio

from loguru import logger
from sqlalchemy import delete, select

from app.core.database import async_session_factory
from app.core.observability.milestone import milestone
from app.core.observability.task_observation import TaskFlow, observe_task
from app.models.project import Project, ProjectStatus, ProjectType
from app.models.writing_binding import WritingBinding
from app.models.writing_outline_node import OutlineNodeStatus, WritingOutlineNode
from app.models.writing_section_draft import WritingSectionDraft


def get_writing_outline_task_name(project_id: int) -> str:
    """获取任务名称"""
    return f"writing_outline_{project_id}"


@milestone(event_key_prefix="writing_outline", task_type="writing_outline", summary_on_success="标书目录生成完成")
@observe_task(flow=TaskFlow.WRITER, task_type="outline")
async def run_writing_outline_task(project_id: int, explicit_retry: bool = False) -> None:
    """执行标书编写目录生成任务

    Args:
        project_id: 项目 ID
    """
    from app.core.arq_runtime import (
        ProjectCancelledError,
        is_project_cancelled,
        raise_if_project_cancelled,
    )
    from app.core.tasks import update_heartbeat

    task_name = get_writing_outline_task_name(project_id)
    update_heartbeat(task_name, stage="starting", progress="0%|初始化")

    if is_project_cancelled(project_id):
        logger.info(f"[写标目录] 项目已取消/删除, 跳过执行: project_id={project_id}")
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return

    async with async_session_factory() as db:
        try:
            from app.services.retry_service import RetryService

            await RetryService(db).mark_task_running(
                project_id,
                node_key=RetryService.NODE_WRITING_OUTLINE,
                stage="starting",
            )
            project = await db.get(Project, project_id)
            if not project:
                logger.error(f"项目不存在: {project_id}")
                return
            if project.project_type != ProjectType.BID_WRITING:
                logger.error(f"项目类型不匹配: {project.project_type}")
                return

            project.status = ProjectStatus.OUTLINING
            await db.commit()

            update_heartbeat(task_name, stage="cleaning", progress="5%|清理旧目录")

            old_node_ids = (await db.execute(
                select(WritingOutlineNode.id).where(
                    WritingOutlineNode.project_id == project_id
                )
            )).scalars().all()

            if old_node_ids:
                ids = list(old_node_ids)
                if explicit_retry:
                    protected_rows = await db.execute(
                        select(WritingOutlineNode.id).where(
                            WritingOutlineNode.project_id == project_id,
                            WritingOutlineNode.status.in_(
                                (OutlineNodeStatus.user_edited, OutlineNodeStatus.finalized)
                            ),
                        )
                    )
                    protected_ids = set(protected_rows.scalars().all())
                    if protected_ids:
                        raise ValueError(
                            "已检测到人工调整目录，显式重试不会自动清理或合并目录，请人工确认后重新生成"
                        )
                await db.execute(
                    delete(WritingSectionDraft).where(
                        WritingSectionDraft.outline_node_id.in_(ids)
                    )
                )
                await db.execute(
                    delete(WritingBinding).where(
                        WritingBinding.outline_node_id.in_(ids)
                    )
                )
                await db.execute(
                    delete(WritingOutlineNode).where(
                        WritingOutlineNode.project_id == project_id,
                        WritingOutlineNode.id.in_(ids),
                    )
                )
                await db.flush()

            from app.core.writer.outline_generator import get_outline_generator
            generator = get_outline_generator(db=db, project_id=project_id)
            generating_state = {"pct": 10, "message": "生成目录中"}

            def set_generating_detail(message: str, current: int, total: int) -> None:
                if message:
                    generating_state["message"] = message
                safe_total = max(total, 1)
                safe_current = max(0, min(current, safe_total))
                pct = 10 + int(safe_current / safe_total * 80)
                generating_state["pct"] = max(generating_state["pct"], pct)
                safe_pct = max(10, min(generating_state["pct"], 90))
                update_heartbeat(
                    task_name,
                    stage="generating",
                    progress=f"{safe_pct}%|{generating_state['message']}",
                )

            set_generating_detail("生成目录中", 0, 1)
            nodes = await generator.generate(
                project_id,
                progress_callback=lambda current, total, message: set_generating_detail(
                    message,
                    current,
                    total,
                ),
            )

            try:
                state = {"pct": 90, "message": "生成章节画像"}

                def set_profiling_detail(message: str, pct: int | None = None) -> None:
                    if message:
                        state["message"] = message
                    if pct is not None:
                        state["pct"] = max(state["pct"], pct)
                    safe_pct = max(90, min(state["pct"], 99))
                    update_heartbeat(
                        task_name,
                        stage="profiling",
                        progress=f"{safe_pct}%|{state['message']}",
                    )

                async def profiling_keepalive() -> None:
                    while True:
                        await asyncio.sleep(1.5)
                        state["pct"] = min(99, state["pct"] + 1)
                        set_profiling_detail(state["message"], state["pct"])

                keepalive_task = asyncio.create_task(profiling_keepalive())
                set_profiling_detail("生成章节画像", 90)
                try:
                    profiled = await generator.generate_chapter_profiles(
                        project_id,
                        progress_callback=lambda current, total, message: set_profiling_detail(
                            message,
                            None if total <= 0 else 90 + int(current / total * 9),
                        ),
                    )
                finally:
                    keepalive_task.cancel()
                    try:
                        await keepalive_task
                    except asyncio.CancelledError:
                        current_task = asyncio.current_task()
                        if current_task and current_task.cancelling():
                            raise
                logger.info(f"章节画像: project={project_id}, profiled={profiled}")
            except Exception as profile_err:
                logger.warning(f"章节画像生成失败(不阻断): {profile_err}")

            project.status = ProjectStatus.OUTLINED
            project.error_message = None
            raise_if_project_cancelled(project_id)
            await db.commit()
            try:
                from app.core.ai import release_project_binding
                await release_project_binding(project_id)
            except Exception:
                pass

            update_heartbeat(
                task_name,
                stage="completed",
                progress=f"100%|目录生成完成，共 {len(nodes)} 个节点",
            )
            await RetryService(db).mark_task_completed(
                project_id,
                node_key=RetryService.NODE_WRITING_OUTLINE,
            )
            logger.info(
                f"标书编写目录生成任务完成: project={project_id}, nodes={len(nodes)}"
            )
            from app.services.express_flow.dispatch import queue_express_advance_if_needed
            await queue_express_advance_if_needed(getattr(project, "run_mode", None), project_id)

        except ProjectCancelledError:
            logger.info(f"标书编写目录生成因项目取消而中止: project={project_id}")
            update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
            try:
                await db.rollback()
            except Exception:
                pass
            return
        except asyncio.CancelledError:
            # CancelledError 继承 BaseException，下面的 except Exception 抓不到它，
            # 此前 ARQ 墙钟超时/worker 关闭会让任务静默消失、不留任何痕迹。
            # 只记痕迹不写 FAILED（取消通常可恢复），且必须 re-raise 保持 ARQ 取消语义。
            from app.core.tasks.lifecycle.interrupt_contract import record_task_interrupted

            try:
                await db.rollback()
            except Exception:
                pass
            await record_task_interrupted(
                project_id=project_id,
                task_name=task_name,
                task_label="标书编写",
            )
            raise
        except Exception as e:
            if is_project_cancelled(project_id):
                update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
                try:
                    await db.rollback()
                except Exception:
                    pass
                return
            logger.error(f"标书编写目录生成失败: {e}", exc_info=True)
            try:
                await db.rollback()
                project = await db.get(Project, project_id)
                if project:
                    project.status = ProjectStatus.FAILED
                    project.error_message = f"目录生成失败: {str(e)[:500]}"
                    await db.commit()
                    # 失败终态必须幂等释放计费预占（契约）
                    try:
                        from app.billing.services.billing_guard import BillingGuard
                        await BillingGuard.on_failed(db, project)
                        await db.commit()
                    except Exception as billing_err:
                        logger.warning(f"[目录生成] 计费释放异常(忽略): {billing_err}")
                    try:
                        from app.core.ai import release_project_binding
                        await release_project_binding(project_id)
                    except Exception:
                        pass
            except Exception as db_err:
                logger.error(f"更新失败状态时出错: {db_err}")

            update_heartbeat(
                task_name,
                stage="failed",
                progress=f"失败|{str(e)[:200]}",
            )
            try:
                from app.services.retry_service import RetryService

                await RetryService(db).mark_task_failed(
                    project_id,
                    node_key=RetryService.NODE_WRITING_OUTLINE,
                    stage="failed",
                    error=str(e),
                )
            except Exception as retry_err:
                logger.warning(f"更新目录生成 TaskRun 失败态异常(忽略): {retry_err}")
