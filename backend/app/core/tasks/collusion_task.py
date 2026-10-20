"""串标分析后台任务

结构对齐 review_task.py：
  - 心跳更新（update_heartbeat）防止任务静默死亡
  - 断点续跑（CollusionAnalyzer 内部 skip_existing 机制）
  - 异常处理与状态回滚

任务名称格式: collusion_project_{project_id}
"""

import asyncio
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.project import Project, ProjectStatus
from app.core.observability.task_observation import observe_task, TaskFlow
from app.core.observability.milestone import milestone


def get_collusion_task_name(project_id: int) -> str:
    """获取串标分析任务名称"""
    return f"collusion_project_{project_id}"


@milestone(event_key_prefix="collusion", task_type="collusion", summary_on_success="串标分析完成")
@observe_task(flow=TaskFlow.COLLUSION, task_type="analyze")
async def run_collusion_task(project_id: int) -> None:
    """执行串标分析后台任务

    完整流程：
    1. 验证项目和文档（至少2份投标文件）
    2. S1a: 招标格式要求提取（可选）
    3. S1b: 各投标文件信息提取（并行）+ S1c: 向量预筛选（与S1b并行）
    4. S3: 深度对比分析（并行，按相似度分级）
    5. S4: 综合研判
    6. 更新项目状态为 COMPLETED

    Args:
        project_id: 项目ID
    """
    from app.core.tasks import update_heartbeat, reset_recovery_count
    from app.core.arq_runtime import (
        ProjectCancelledError,
        is_project_cancelled,
        raise_if_project_cancelled,
    )
    from app.core.tasks.cancellation import make_project_cancel_check

    task_name = get_collusion_task_name(project_id)
    last_stage = "启动"

    logger.info(f"[串标任务] ========== 开始 project_id={project_id} ==========")
    update_heartbeat(task_name, stage="启动", progress="初始化中")

    if is_project_cancelled(project_id):
        logger.info(f"[串标任务] 项目已取消/删除, 跳过执行: project_id={project_id}")
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return
    cancel_check = make_project_cancel_check(project_id)

    async with async_session_factory() as db:
        try:
            # ── 步骤1: 验证项目和文档 ──────────────────────────────────────────
            cancel_check()
            update_heartbeat(task_name, stage="步骤1", progress="验证项目和文档")
            project = await _get_project(db, project_id)
            if not project:
                logger.error(f"[串标任务] 项目不存在: {project_id}")
                return

            bid_doc_count = await _count_bid_docs(db, project_id)
            if bid_doc_count < 2:
                await _fail_project(db, project, f"串标分析需要至少2份投标文件，当前 {bid_doc_count} 份")
                return

            logger.info(f"[串标任务] 文档验证通过: 共 {bid_doc_count} 份投标文件")

            # ── 步骤2-5: 运行分析编排器 ────────────────────────────────────────
            cancel_check()
            from app.core.collusion.analyzer import CollusionAnalyzer

            def heartbeat_cb(
                stage: str,
                progress: str,
                extra: Optional[dict[str, object]] = None,
            ) -> None:
                nonlocal last_stage
                last_stage = stage or last_stage
                update_heartbeat(task_name, stage=stage, progress=progress, extra=extra)

            analyzer = CollusionAnalyzer(
                db=db,
                project_id=project_id,
                heartbeat_callback=heartbeat_cb,
                cancel_check=cancel_check,
            )

            synthesis_result = await analyzer.run()
            cancel_check()
            logger.info(
                f"[串标任务] 分析完成, 整体风险={synthesis_result.get('overall_risk')}"
            )

            # ── 步骤6: 更新项目状态 + 计费结算 ──────────────────────────────────
            cancel_check()
            update_heartbeat(task_name, stage="步骤6", progress="更新状态")
            cancel_check()
            project.status = ProjectStatus.COMPLETED
            project.error_message = None
            try:
                from app.billing.services.billing_guard import BillingGuard
                cancel_check()
                await BillingGuard.on_completed(db, project)
            except Exception as billing_err:
                logger.warning(f"[串标任务] 计费结算异常(不影响项目状态): {billing_err}")
            cancel_check()
            await db.commit()

            try:
                from app.core.ai import release_project_binding
                await release_project_binding(project_id)
            except Exception:
                pass

            reset_recovery_count(task_name)
            try:
                from app.models.task_run import TaskRunStatus
                from app.services.flow_retry_service import mark_collusion_task_run_by_task_name

                await mark_collusion_task_run_by_task_name(
                    db,
                    project_id=project_id,
                    task_name=task_name,
                    status=TaskRunStatus.completed.value,
                    stage="completed",
                    last_error=None,
                )
            except Exception as mark_err:
                logger.warning(f"[串标任务] 回写显式重试完成状态失败(忽略): {mark_err}")
            logger.info(f"[串标任务] ========== 完成 project_id={project_id} ==========")

        except ProjectCancelledError:
            logger.info(
                f"[串标任务] 任务因项目取消而中止: project_id={project_id}"
            )
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
                task_label="串标任务",
            )
            raise
        except Exception as e:
            # 异常发生时若项目已删除, 按取消路径收尾
            if is_project_cancelled(project_id):
                logger.info(
                    f"[串标任务] 异常时项目已取消, 按取消路径收尾: "
                    f"project_id={project_id}, err={str(e)[:200]}"
                )
                update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
                try:
                    await db.rollback()
                except Exception:
                    pass
                return

            from app.core.collusion.analyzer import ExtractionQualityError

            if isinstance(e, ExtractionQualityError):
                logger.warning(f"[串标任务] 提取质量不达标: {e}")
                user_msg = str(e)
            else:
                logger.error(f"[串标任务] 异常: {e}", exc_info=True)
                user_msg = "系统处理异常，请稍后重试或联系管理员。"

            try:
                await db.rollback()
            except Exception:
                pass
            try:
                async with async_session_factory() as err_db:
                    project = await _get_project(err_db, project_id)
                    if project:
                        try:
                            from app.billing.services.billing_guard import BillingGuard
                            await BillingGuard.on_failed(err_db, project)
                        except Exception as billing_err:
                            logger.warning(f"[串标任务] 计费释放异常: {billing_err}")
                        await _fail_project(err_db, project, user_msg, stage=last_stage)
            except Exception as inner_e:
                logger.error(f"[串标任务] 更新失败状态时出错: {inner_e}")


# ── 私有辅助 ──────────────────────────────────────────────────────────────────

async def _get_project(db: AsyncSession, project_id: int) -> Optional[Project]:
    result = await db.execute(select(Project).where(Project.id == project_id))
    return result.scalar_one_or_none()


async def _count_bid_docs(db: AsyncSession, project_id: int) -> int:
    from app.models.document import Document, DocumentType
    from sqlalchemy import func

    result = await db.execute(
        select(func.count()).where(
            Document.project_id == project_id,
            Document.doc_type == DocumentType.BID,
        )
    )
    return result.scalar() or 0


async def _fail_project(
    db: AsyncSession,
    project: Project,
    reason: str,
    stage: str = "failed",
) -> None:
    logger.warning(f"[串标任务] 项目失败: id={project.id}, 原因: {reason}")
    project.status = ProjectStatus.FAILED
    project.error_message = reason[:1000]
    await db.commit()
    try:
        from app.models.task_run import TaskRunStatus
        from app.services.flow_retry_service import mark_collusion_task_run_by_task_name

        await mark_collusion_task_run_by_task_name(
            db,
            project_id=project.id,
            task_name=get_collusion_task_name(project.id),
            status=TaskRunStatus.failed.value,
            stage=stage,
            last_error=reason,
        )
    except Exception as mark_err:
        logger.warning(f"[串标任务] 回写显式重试失败状态失败(忽略): {mark_err}")
    try:
        from app.core.ai import release_project_binding
        await release_project_binding(project.id)
    except Exception:
        pass
