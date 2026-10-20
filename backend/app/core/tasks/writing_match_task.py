"""标书编写 -- 素材匹配后台任务(章节拉取式架构)

触发条件: 用户在素材关联页触发匹配
流程: 目录向量化 -> 约束分类 -> 章节拉取裁决 -> 素材关联 -> 一致性校验
进度阶段: starting -> vectorizing -> classifying -> retrieving -> materials
           -> validating -> saving -> completed / failed
项目状态: MATCHING -> MATCHED
"""

import asyncio

from loguru import logger

from app.core.database import async_session_factory
from app.core.writer.material_matching.binding_persistence import prepare_binding_rematch
from app.models.project import Project, ProjectStatus, ProjectType


def get_writing_match_task_name(project_id: int) -> str:
    """获取任务名称"""
    return f"writing_match_{project_id}"


async def run_writing_match_task(
    project_id: int,
    explicit_retry: bool = False,
    reset_reviewed_bindings: bool = False,
) -> None:
    """执行标书编写素材匹配任务

 Args:
 project_id: 项目 ID
 explicit_retry: 失败重试路径，仅清理未确认建议、保留已确认成果。
 reset_reviewed_bindings: 用户显式"重置审核并重新匹配"，清空全部绑定。
 默认 False，仅重算 suggested，保留人工 confirmed 与 rejected。
 """
    from app.core.tasks import update_heartbeat
    from app.core.arq_runtime import ProjectCancelledError, is_project_cancelled

    task_name = get_writing_match_task_name(project_id)
    update_heartbeat(task_name, stage="starting", progress="0%|初始化")

    if is_project_cancelled(project_id):
        logger.info(f"[写标匹配] 项目已取消/删除, 跳过执行: project_id={project_id}")
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return

    async with async_session_factory() as db:
        try:
            from app.services.retry_service import RetryService

            await RetryService(db).mark_task_running(
                project_id,
                node_key=RetryService.NODE_WRITING_MATCH,
                stage="starting",
            )
            project = await db.get(Project, project_id)
            if not project:
                logger.error(f"项目不存在: {project_id}")
                return
            if project.project_type != ProjectType.BID_WRITING:
                logger.error(f"项目类型不匹配: {project.project_type}")
                return

            project.status = ProjectStatus.MATCHING
            await db.commit()

            # 绑定清理口径按决策粒度单一化到 binding_persistence:
            # 默认仅重算 suggested、保留人工 confirmed/rejected；explicit_retry 保留
            # confirmed；reset_reviewed_bindings 清空全部。
            deleted = await prepare_binding_rematch(
                db,
                project_id,
                reset_reviewed_bindings=reset_reviewed_bindings,
                explicit_retry=explicit_retry,
            )
            if deleted > 0:
                logger.info(f"旧绑定已清理: project={project_id}, deleted={deleted}")
            else:
                logger.debug(f"无旧绑定需清理: project={project_id}")

            def progress_callback(
                stage: str,
                current: int,
                total: int,
                message: str = "",
                *args: object,
            ) -> None:
                stage_weights = {
                    "vectorizing": (0, 12),
                    "classifying": (12, 25),
                    "retrieving": (25, 60),
                    "materials": (60, 80),
                    "validating": (80, 90),
                    "saving": (90, 98),
                    "completed": (100, 100),
                }
                start, end = stage_weights.get(stage, (0, 100))
                ratio = current / total if total > 0 else 0
                pct = int(start + (end - start) * ratio)
                update_heartbeat(
                    task_name,
                    stage=stage,
                    progress=f"{pct}%|{message}",
                )

            def vectorizing_progress_callback(
                current: int,
                total: int,
                message: str,
            ) -> None:
                start, end = 5, 12
                ratio = current / total if total > 0 else 0
                pct = int(start + (end - start) * ratio)
                update_heartbeat(
                    task_name,
                    stage="vectorizing",
                    progress=f"{pct}%|{message}",
                )

            update_heartbeat(task_name, stage="matching", progress="5%|加载数据")

            update_heartbeat(task_name, stage="vectorizing", progress="5%|准备目录向量")
            from app.services.writing_service import WritingService
            vectorize_service = WritingService(db)
            vectorized_count = await vectorize_service._vectorize_outline_nodes(
                project_id,
                progress_callback=vectorizing_progress_callback,
            )
            update_heartbeat(
                task_name,
                stage="vectorizing",
                progress=f"12%|目录向量准备完成 {vectorized_count} 个节点",
            )

            from app.core.writer.material_matcher import get_material_matcher
            matcher = get_material_matcher(db=db, project_id=project_id)
            bindings = await matcher.match(
                project_id, progress_callback=progress_callback
            )

            project.status = ProjectStatus.MATCHED
            project.error_message = None
            await db.commit()
            try:
                from app.core.ai import release_project_binding
                await release_project_binding(project_id)
            except Exception:
                pass

            update_heartbeat(
                task_name,
                stage="completed",
                progress=(
                    "100%|匹配完成，当前无可用绑定，可直接进入内容编辑"
                    if len(bindings) == 0
                    else f"100%|匹配完成，共 {len(bindings)} 条绑定"
                ),
            )
            await RetryService(db).mark_task_completed(
                project_id,
                node_key=RetryService.NODE_WRITING_MATCH,
            )
            logger.info(
                f"标书编写素材匹配任务完成: project={project_id}, "
                f"bindings={len(bindings)}"
            )
            from app.services.express_flow.dispatch import queue_express_advance_if_needed
            await queue_express_advance_if_needed(getattr(project, "run_mode", None), project_id)

        except ProjectCancelledError:
            # 项目被并发删除时走静默清理路径，不写 FAILED、不写 last_error，
            # 避免污染重试队列与状态机；与 修复模式对齐。
            logger.info(
                f"[写标匹配] 项目已删除/取消，中断匹配并静默清理: project_id={project_id}"
            )
            try:
                await db.rollback()
            except Exception:
                pass
            update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
            try:
                from app.core.ai import release_project_binding
                await release_project_binding(project_id)
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
            logger.error(f"标书编写素材匹配失败: {e}", exc_info=True)
            try:
                await db.rollback()
                project = await db.get(Project, project_id)
                if project:
                    project.status = ProjectStatus.FAILED
                    project.error_message = f"素材匹配失败: {str(e)[:500]}"
                    await db.commit()
                    # 失败终态必须幂等释放计费预占（契约）
                    try:
                        from app.billing.services.billing_guard import BillingGuard
                        await BillingGuard.on_failed(db, project)
                        await db.commit()
                    except Exception as billing_err:
                        logger.warning(f"[素材匹配] 计费释放异常(忽略): {billing_err}")
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
                    node_key=RetryService.NODE_WRITING_MATCH,
                    stage="failed",
                    error=str(e),
                )
            except Exception as retry_err:
                logger.warning(f"更新素材匹配 TaskRun 失败态异常(忽略): {retry_err}")
