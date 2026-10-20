"""标书编写 -- 全文内容生成后台任务

触发条件: 用户在内容编辑页点击生成 / 自动触发
流程: 逐叶子节点生成内容草稿(三分法路由)
进度阶段: composing -> completed / failed
项目状态: WRITING -> COMPOSED
"""

import asyncio
import json
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.project import Project, ProjectStatus, ProjectType
from app.models.writing_render_artifact import WritingRenderArtifact
from app.core.observability.task_observation import observe_task, TaskFlow
from app.core.observability.milestone import milestone
from app.core.ai.task_prompt_snapshot import with_task_prompt_snapshot


def get_writing_compose_task_name(project_id: int) -> str:
    """获取任务名称"""
    return f"writing_compose_{project_id}"


async def _refresh_consistency_cache_after_compose(
    db: AsyncSession,
    project_id: int,
) -> int:
    """职责: 内容生成完成后刷新一致性检查缓存。
    不做: 不生成 HTML 预览或 DOCX 渲染产物。
    """
    from app.core.writer.consistency_checker import get_consistency_checker

    checker = get_consistency_checker(db=db, project_id=project_id)
    issues = await checker.check(project_id)

    result = await db.execute(
        select(WritingRenderArtifact).where(
            WritingRenderArtifact.project_id == project_id,
        )
    )
    artifact = result.scalars().first()
    if artifact is None:
        artifact = WritingRenderArtifact(
            project_id=project_id,
            render_version=1,
        )
        db.add(artifact)

    artifact.consistency_issues_json = json.dumps(issues, ensure_ascii=False)
    artifact.consistency_checked_at = datetime.now(timezone.utc)
    artifact.consistency_render_version = artifact.render_version
    await db.commit()
    return len(issues)


@milestone(event_key_prefix="writing_compose", task_type="writing_compose", summary_on_success="标书全文生成完成")
@observe_task(flow=TaskFlow.WRITER, task_type="compose")
@with_task_prompt_snapshot("writing")
async def run_writing_compose_task(project_id: int, explicit_retry: bool = False) -> None:
    """执行标书编写全文生成任务"""
    from app.core.tasks import update_heartbeat
    from app.core.arq_runtime import ProjectCancelledError, is_project_cancelled, raise_if_project_cancelled
    from app.core.observability.degradation_aggregator import (
        set_aggregator_for_task, get_aggregator, set_denominator,
        record_degradation,
    )

    task_name = get_writing_compose_task_name(project_id)
    update_heartbeat(task_name, stage="starting", progress="0%|初始化")

    if is_project_cancelled(project_id):
        logger.info(f"[写标生成] 项目已取消/删除, 跳过执行: project_id={project_id}")
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return

    # P1.3: 任务级降级聚合器
    set_aggregator_for_task(flow="writer_compose")

    async with async_session_factory() as db:
        try:
            from app.services.retry_service import RetryService

            await RetryService(db).mark_task_running(
                project_id,
                node_key=RetryService.NODE_WRITING_COMPOSE,
                stage="starting",
            )
            project = await db.get(Project, project_id)
            if not project:
                logger.error(f"项目不存在: {project_id}")
                return
            if project.project_type != ProjectType.BID_WRITING:
                logger.error(f"项目类型不匹配: {project.project_type}")
                return

            project.status = ProjectStatus.WRITING
            await db.commit()

            def progress_callback(
                stage: str,
                current: int,
                total: int,
                message: str = "",
                *args: object,
            ) -> None:
                if total > 0:
                    pct = int(current / total * 100)
                else:
                    pct = 0
                update_heartbeat(
                    task_name,
                    stage=stage,
                    progress=f"{pct}%|[{current}/{total}] {message}",
                )

            update_heartbeat(task_name, stage="composing", progress="0%|开始生成")

            from app.core.writer.section_composer import get_section_composer
            composer = get_section_composer(db=db, project_id=project_id)
            raise_if_project_cancelled(project_id)
            drafts = await composer.compose_all(
                project_id,
                progress_callback=progress_callback,
                skip_existing_drafts=explicit_retry,
            )
            raise_if_project_cancelled(project_id)
            update_heartbeat(
                task_name,
                stage="sweeping",
                progress="98%|正在回查未完成章节",
            )
            from app.core.writer.composer.compose_sweep import sweep_incomplete_sections

            repaired_drafts = await sweep_incomplete_sections(composer, project_id)
            raise_if_project_cancelled(project_id)
            if repaired_drafts:
                drafts.extend(repaired_drafts)
                logger.info(
                    "标书编写全文生成回头看完成: project={}, repaired={}",
                    project_id,
                    len(repaired_drafts),
                )

            # 标记分母: 本次任务处理的章节总数, 供降级比率判定
            set_denominator("compose_failed", len(drafts))

            project.status = ProjectStatus.COMPOSED
            project.error_message = None
            raise_if_project_cancelled(project_id)
            await db.commit()

            try:
                update_heartbeat(
                    task_name,
                    stage="checking",
                    progress="99%|生成完成，正在执行一致性检查",
                )
                issue_count = await _refresh_consistency_cache_after_compose(
                    db,
                    project_id,
                )
                raise_if_project_cancelled(project_id)
                logger.info(
                    f"标书编写全文一致性检查完成: project={project_id}, issues={issue_count}"
                )
            except Exception as consistency_err:
                logger.warning(
                    f"标书编写全文一致性检查降级: project={project_id}, err={consistency_err}",
                    exc_info=True,
                )
                record_degradation(
                    kind="consistency_check_failed",
                    ref=f"project_{project_id}",
                    reason=f"{type(consistency_err).__name__}: {str(consistency_err)[:120]}",
                )

            try:
                from app.billing.services.billing_guard import BillingGuard
                await BillingGuard.on_completed(db, project)
            except Exception as billing_err:
                logger.warning(f"[标书编写] 计费结算异常(不影响项目状态): {billing_err}")

            # P1.3: 任务末尾聚合降级信号并写入 heartbeat; 超阈值时 error_message 添加前缀
            summary = None
            agg = get_aggregator()
            if agg is not None:
                summary = agg.summarize()
                if summary.severe_kinds:
                    project.error_message = (
                        f"[降级警告] {summary.text}"[:500]
                    )

            await db.commit()
            try:
                from app.core.ai import release_project_binding
                await release_project_binding(project_id)
            except Exception:
                pass

            complete_msg = f"100%|生成完成，共 {len(drafts)} 章节"
            if summary is not None and summary.text:
                complete_msg = f"{complete_msg} | 降级: {summary.text}"
            update_heartbeat(
                task_name,
                stage="completed",
                progress=complete_msg,
            )
            await RetryService(db).mark_task_completed(
                project_id,
                node_key=RetryService.NODE_WRITING_COMPOSE,
            )
            logger.info(
                f"标书编写全文生成任务完成: project={project_id}, "
                f"drafts={len(drafts)}"
                + (f", degradation={summary.text}" if summary and summary.text else "")
            )

        except ProjectCancelledError:
            logger.info(f"标书编写全文生成因项目取消而中止: project={project_id}")
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
            logger.error(f"标书编写全文生成失败: {e}", exc_info=True)
            # 并发协程可能已使原 session 进入非法状态(isce),
            # 先尽力回滚原 session, 失败态更新一律走独立 session,
            # 禁止复用可能损坏的并发 session 以免二次崩溃掩盖首因。
            try:
                await db.rollback()
            except Exception as rb_err:
                logger.warning(f"[标书编写] 原 session 回滚失败(忽略): {rb_err}")

            async with async_session_factory() as err_db:
                try:
                    project = await err_db.get(Project, project_id)
                    if project:
                        try:
                            from app.billing.services.billing_guard import BillingGuard
                            await BillingGuard.on_failed(err_db, project)
                        except Exception as billing_err:
                            logger.warning(f"[标书编写] 计费释放异常: {billing_err}")
                        project.status = ProjectStatus.FAILED
                        project.error_message = f"全文生成失败: {str(e)[:500]}"
                        await err_db.commit()
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

                    await RetryService(err_db).mark_task_failed(
                        project_id,
                        node_key=RetryService.NODE_WRITING_COMPOSE,
                        stage="failed",
                        error=str(e),
                    )
                except Exception as retry_err:
                    logger.warning(f"更新全文生成 TaskRun 失败态异常(忽略): {retry_err}")
