"""标书编写 -- 约束提取后台任务

触发条件: 招标文件解析完成后
流程: 三层提取 -> 评分项权威提取 -> 渲染配置解析 -> 行业画像生成 -> 标书结构分析
进度阶段: extracting_* -> dedup -> saving -> scoring_authority
           -> render_profile -> industry_profile -> structure_analysis -> completed
项目状态: PARSING -> EXTRACTING -> EXTRACTED
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from loguru import logger
from sqlalchemy import delete, select

from app.core.database import (
    async_session_factory,
    is_dead_connection_error,
    with_db_reconnect,
)
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus, ProjectType
from app.core.writer.clause_extract_progress import (
    build_writing_extract_progress_callback,
)
from app.core.writer.clause_extractor import get_clause_extractor
from app.core.writer.industry_profiler import get_industry_profiler
from app.core.writer.progress_contract import (
    get_stage_start_pct,
    get_stage_weight_range,
)
from app.core.observability.task_observation import observe_task, TaskFlow
from app.core.observability.milestone import milestone

T = TypeVar("T")


def get_writing_extract_task_name(project_id: int) -> str:
    """获取任务名称"""
    return f"writing_extract_{project_id}"


# with_db_reconnect 必须在 milestone/observe_task 之外(最外层),
# 否则首次失败已被 milestone 写入失败事件, 重试成功后状态不一致
@with_db_reconnect(max_retries=1)
@milestone(event_key_prefix="writing_extract", task_type="writing_extract", summary_on_success="标书约束提取完成")
@observe_task(flow=TaskFlow.WRITER, task_type="extract")
async def run_writing_extract_task(
    project_id: int,
    explicit_retry: bool = False,
    expected_attempt: int | None = None,
) -> None:
    """执行标书编写约束提取任务

    Args:
        project_id: 项目 ID
    """
    from app.core.tasks import update_heartbeat
    from app.core.arq_runtime import ProjectCancelledError, is_project_cancelled, raise_if_project_cancelled

    task_name = get_writing_extract_task_name(project_id)
    update_heartbeat(task_name, stage="starting", progress="初始化中")

    if is_project_cancelled(project_id):
        logger.info(f"[写标提取] 项目已取消/删除, 跳过执行: project_id={project_id}")
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return

    prefetch_tasks = None
    async with async_session_factory() as db:
        try:
            from app.services.retry_service import RetryService

            retry_svc = RetryService(db)
            if expected_attempt is not None:
                latest = await retry_svc._latest_run(  # noqa: SLF001 - task owns retry attempt guard
                    project_id,
                    RetryService.NODE_WRITING_EXTRACT,
                )
                if latest is None or latest.attempt != expected_attempt:
                    logger.warning(
                        "[写标提取] 跳过过期 attempt: project={}, expected={}, latest={}",
                        project_id,
                        expected_attempt,
                        getattr(latest, "attempt", None),
                    )
                    update_heartbeat(task_name, stage="cancelled", progress="旧提取任务已过期")
                    return

            await retry_svc.mark_task_running(
                project_id,
                node_key=RetryService.NODE_WRITING_EXTRACT,
                stage="starting",
            )
            # 1. 验证项目
            project = await db.get(Project, project_id)
            if not project:
                logger.error(f"项目不存在: {project_id}")
                return
            if project.project_type != ProjectType.BID_WRITING:
                logger.error(f"项目类型不匹配: {project.project_type}")
                return

            # 2. 查找招标文件
            result = await db.execute(
                select(Document).where(
                    Document.project_id == project_id,
                    Document.doc_type == DocumentType.TENDER,
                )
            )
            tender_doc = result.scalars().first()
            if not tender_doc:
                raise ValueError(f"未找到招标文件: project_id={project_id}")

            # 3. 更新状态为 EXTRACTING + 清理旧提取数据(支持重跑)
            project.status = ProjectStatus.EXTRACTING
            from app.models.writing_render_profile import WritingRenderProfile
            from app.models.writing_clause import WritingClause

            old_rp = None
            if not explicit_retry:
                old_rp = await db.execute(
                    delete(WritingRenderProfile).where(
                        WritingRenderProfile.project_id == project_id
                    )
                )
            old_cl_conditions = [WritingClause.project_id == project_id]
            if explicit_retry:
                old_cl_conditions.extend([
                    WritingClause.confirmed == 0,
                    WritingClause.is_manual == 0,
                ])
            old_cl = await db.execute(delete(WritingClause).where(*old_cl_conditions))
            await db.commit()
            old_rp_count = old_rp.rowcount if old_rp is not None else 0
            if old_rp_count or old_cl.rowcount:
                logger.info(
                    f"清理旧提取数据: project={project_id}, "
                    f"render_profiles={old_rp_count}, clauses={old_cl.rowcount}"
                )

            from app.core.writer.extract_prefetch import start_extract_prefetch

            prefetch_tasks = start_extract_prefetch(project_id, tender_doc.id)

            # 4. 定义进度回调
            # 提取进度经 aggregator 改写为单调 stage="extracting"。
            # 后提取为单次过正文 (extracting_clauses)，分母=批次数;
            # 去重仍三层 gather，但走 dedup 权重，不进 aggregator。
            # 非提取 stage (dedup / saving / render_profile / ...) 仍按权重换算。
            progress_callback = build_writing_extract_progress_callback(
                task_name=task_name,
                heartbeat=update_heartbeat,
            )

            def set_stage_detail(
                stage: str,
                message: str,
                pct: int | None = None,
            ) -> None:
                start, end = get_stage_weight_range(stage)
                max_running_pct = end - 1 if end > start else end
                safe_pct = start if pct is None else pct
                safe_pct = max(start, min(safe_pct, max_running_pct))
                update_heartbeat(
                    task_name,
                    stage=stage,
                    progress=f"{safe_pct}%|{message}",
                )

            async def run_with_stage_keepalive(
                stage: str,
                initial_message: str,
                runner: Callable[[Callable[[str, int | None], None]], Awaitable[T]],
            ) -> T:
                start, end = get_stage_weight_range(stage)
                max_running_pct = end - 1 if end > start else end
                state = {
                    "message": initial_message,
                    "pct": get_stage_start_pct(stage),
                }
                set_stage_detail(stage, state["message"], state["pct"])

                async def keepalive() -> None:
                    while True:
                        await asyncio.sleep(1.5)
                        if max_running_pct > start:
                            state["pct"] = min(max_running_pct, state["pct"] + 1)
                        set_stage_detail(stage, state["message"], state["pct"])

                def stage_progress(message: str, pct: int | None = None) -> None:
                    if message:
                        state["message"] = message
                    if pct is not None:
                        state["pct"] = pct
                    set_stage_detail(stage, state["message"], state["pct"])

                keepalive_task = asyncio.create_task(keepalive())
                try:
                    return await runner(stage_progress)
                finally:
                    keepalive_task.cancel()
                    try:
                        await keepalive_task
                    except asyncio.CancelledError:
                        current_task = asyncio.current_task()
                        if current_task and current_task.cancelling():
                            raise

            # 5. 三层约束提取 + 去重 + 保存
            update_heartbeat(task_name, stage="extracting", progress="0%|开始提取约束")
            extractor = get_clause_extractor(
                db=db,
                progress_callback=progress_callback,
                project_id=project_id,
            )
            clauses = await extractor.extract(
                project_id=project_id,
                document_id=tender_doc.id,
            )
            logger.info(f"约束提取完成: {len(clauses)} 条")

            # 6. 评分项权威提取(独立长阶段，须在 render_profile 前发心跳，避免前端误判卡死)
            # 失败不影响主流程(保留 LLM 结果); 成功则同步更新内存 clauses 供画像/结构分析消费权威数据
            from app.core.config import settings as _writing_cfg

            async def _run_scoring_authority(
                stage_progress: Callable[[str, int | None], None],
            ) -> list:
                nonlocal_clauses = clauses
                if not _writing_cfg.WRITING_SCORING_AUTHORITY_ENABLED:
                    stage_progress("跳过评分项权威提取", None)
                    return nonlocal_clauses
                try:
                    from app.core.writer.extract_prefetch import await_scoring_prefetch
                    from app.core.writer.scoring_source import (
                        apply_scoring_authority,
                        replace_scoring_clauses_with_authority,
                    )

                    stage_progress("提取评分项结构", None)
                    non_scoring = [
                        c for c in nonlocal_clauses if c.sub_type != "scoring_criterion"
                    ]
                    outcome = await await_scoring_prefetch(prefetch_tasks)
                    if outcome is None:
                        authoritative = await replace_scoring_clauses_with_authority(
                            db, project_id, tender_doc.id
                        )
                    else:
                        authoritative = await apply_scoring_authority(
                            db, project_id, tender_doc.id, outcome
                        )
                    if authoritative is not None:
                        nonlocal_clauses = non_scoring + authoritative
                        stage_progress(
                            f"已替换 {len(authoritative)} 条权威评分项",
                            None,
                        )
                        logger.info(
                            f"评分项权威替换完成: {len(authoritative)} 条权威评分项"
                        )
                    else:
                        stage_progress("未产出权威评分项，保留 LLM 结果", None)
                except Exception as scoring_err:
                    logger.warning(
                        f"评分项权威提取失败(忽略，保留LLM结果): {scoring_err}"
                    )
                    stage_progress("评分项权威提取失败，已回退 LLM 结果", None)
                    try:
                        from app.core.observability.degradation_aggregator import (
                            record_degradation,
                        )

                        record_degradation(
                            kind="scoring_authority_fallback",
                            ref=f"project_{project_id}",
                            reason=f"{type(scoring_err).__name__}: {str(scoring_err)[:200]}",
                            document_id=tender_doc.id,
                        )
                    except Exception:
                        pass
                return nonlocal_clauses

            clauses = await run_with_stage_keepalive(
                "scoring_authority",
                "提取评分项",
                _run_scoring_authority,
            )

            # 6.1 散落评分条目关联收口: 把其它标签下与权威评分项等价的条目双向关联(不搬家不删除)
            if _writing_cfg.WRITING_SCORING_LINK_ENABLED:
                try:
                    from app.core.writer.scoring_clause_link import (
                        link_scattered_scoring_clauses,
                    )

                    fresh_clauses = list(
                        (
                            await db.execute(
                                select(WritingClause).where(
                                    WritingClause.project_id == project_id
                                )
                            )
                        ).scalars().all()
                    )
                    linked = link_scattered_scoring_clauses(fresh_clauses)
                    if linked:
                        await db.commit()
                        clauses = fresh_clauses
                        logger.info(f"评分散落条目关联收口: {linked} 对")
                except Exception as link_err:
                    logger.warning(f"评分散落条目关联失败(忽略): {link_err}")

            # 6.2 渲染配置解析
            # BUG-XXX: 原实现直接 update_heartbeat 后调用 LLM，没有 keepalive，
            # 导致整个 LLM 等待期间心跳卡在起点 pct=90，前端 analysis_target 恒为 0。
            # 与其它分析阶段统一，改为 run_with_stage_keepalive 驱动。
            from app.core.writer.render_profile_currentness import (
                RENDER_PROFILE_SOURCE_SUB_TYPES,
            )

            async def _run_render_profile(
                stage_progress: Callable[[str, int | None], None],
            ) -> None:
                stage_progress("解析渲染配置", None)
                rp_sources = [
                    c for c in clauses if c.sub_type in RENDER_PROFILE_SOURCE_SUB_TYPES
                ]
                if rp_sources:
                    await extractor.parse_render_profile(project_id, rp_sources)
                    stage_progress("渲染配置解析完成", None)
                    logger.info("渲染配置解析完成")
                else:
                    stage_progress("无格式规定，使用默认配置", None)
                    logger.info("无格式/提交规定约束，使用默认渲染配置")

            await run_with_stage_keepalive(
                "render_profile",
                "解析渲染配置",
                _run_render_profile,
            )

            # 6.3 写作消费分级: 标记 reference 约束, 后续 matcher/coverage 按折叠口径消费
            if _writing_cfg.WRITING_RELEVANCE_PASS_ENABLED:
                try:
                    from app.core.writer.writing_relevance import (
                        classify_writing_relevance,
                    )

                    await classify_writing_relevance(
                        clauses,
                        llm_client=extractor.llm_client,
                    )
                    await db.commit()
                    logger.info("写作消费分级完成")
                except Exception as relevance_err:
                    logger.warning(
                        f"写作消费分级失败(忽略，保守全部进入正文消费): {relevance_err}"
                    )

            # 7. 行业画像生成
            profiler = get_industry_profiler(db=db, project_id=project_id)
            profile_clause = await run_with_stage_keepalive(
                "industry_profile",
                "生成行业画像",
                lambda stage_progress: profiler.generate(
                    project_id=project_id,
                    document_id=tender_doc.id,
                    clauses=clauses,
                    progress_callback=stage_progress,
                ),
            )
            logger.info(f"行业画像已生成: clause_id={profile_clause.id}")

            # 8. 标书结构分析（判定技术标/商务标分卷结构）
            all_clauses_with_profile = list(clauses) + [profile_clause]
            structure_clause = await run_with_stage_keepalive(
                "structure_analysis",
                "分析标书结构",
                lambda stage_progress: extractor.analyze_bid_structure(
                    project_id=project_id,
                    document_id=tender_doc.id,
                    clauses=all_clauses_with_profile,
                    progress_callback=stage_progress,
                ),
            )
            logger.info(
                f"标书结构分析完成: clause_id={structure_clause.id}"
            )

            # 9. 目录原文结构化提取
            async def _run_directory_extract(
                stage_progress: Callable[[str, int | None], None],
            ):
                from app.core.writer.bid_directory_extractor import save_directory_spec
                from app.core.writer.extract_prefetch import await_directory_prefetch

                collected = await await_directory_prefetch(prefetch_tasks)
                if collected is not None:
                    stage_progress("保存目录提取结果", None)
                    return await save_directory_spec(
                        db,
                        project_id,
                        tender_doc.id,
                        collected.spec,
                        extra_metadata=collected.extra_metadata,
                    )
                return await extractor.extract_bid_directory(
                    project_id=project_id,
                    document_id=tender_doc.id,
                    progress_callback=stage_progress,
                )

            dir_clause = await run_with_stage_keepalive(
                "directory_extract",
                "提取目录结构",
                _run_directory_extract,
            )
            logger.info(
                f"目录原文提取完成: clause_id={dir_clause.id}"
            )

            # 10. 更新项目状态
            if expected_attempt is not None:
                latest = await retry_svc._latest_run(  # noqa: SLF001 - task owns retry attempt guard
                    project_id,
                    RetryService.NODE_WRITING_EXTRACT,
                )
                if (
                    latest is None
                    or latest.attempt != expected_attempt
                    or not RetryService.is_retry_running_status(latest.status)
                ):
                    logger.warning(
                        "[写标提取] 完成提交被跳过: project={}, expected={}, latest={}, status={}",
                        project_id,
                        expected_attempt,
                        getattr(latest, "attempt", None),
                        getattr(latest, "status", None),
                    )
                    update_heartbeat(task_name, stage="cancelled", progress="旧提取任务完成结果已忽略")
                    return
            project.status = ProjectStatus.EXTRACTED
            await db.commit()
            try:
                from app.core.ai import release_project_binding
                await release_project_binding(project_id)
            except Exception:
                pass

            update_heartbeat(
                task_name,
                stage="completed",
                progress=f"100%|提取完成，共 {len(clauses) + 3} 条约束",
            )
            raise_if_project_cancelled(project_id)
            await RetryService(db).mark_task_completed(
                project_id,
                node_key=RetryService.NODE_WRITING_EXTRACT,
            )
            logger.info(
                f"标书编写约束提取任务完成: project={project_id}, "
                f"clauses={len(clauses) + 3}"
            )
            from app.services.express_flow.dispatch import queue_express_advance_if_needed
            await queue_express_advance_if_needed(getattr(project, "run_mode", None), project_id)

        except ProjectCancelledError:
            logger.info(f"标书编写约束提取因项目取消而中止: project={project_id}")
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
            # DB 死连接(asyncpg connection is closed)属于纯基建抖动,
            # 必须 reraise 让外层 with_db_reconnect 装饰器接管重试;
            # 此处不写 FAILED 状态 / 不 mark_task_failed, 避免状态污染
            if is_dead_connection_error(e):
                logger.warning(
                    f"[写标提取] 检测到 DB 死连接, 交由外层装饰器重试: project={project_id}, err={str(e)[:200]}"
                )
                raise
            if is_project_cancelled(project_id):
                update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
                try:
                    await db.rollback()
                except Exception:
                    pass
                return
            logger.error(f"标书编写约束提取失败: {e}", exc_info=True)
            try:
                await db.rollback()
                from app.services.retry_service import RetryService
                retry_svc = RetryService(db)
                if expected_attempt is not None:
                    latest = await retry_svc._latest_run(  # noqa: SLF001 - task owns retry attempt guard
                        project_id,
                        RetryService.NODE_WRITING_EXTRACT,
                    )
                    if latest is None or latest.attempt != expected_attempt:
                        logger.warning(
                            "[写标提取] 过期 attempt 失败结果已忽略: project={}, expected={}, latest={}",
                            project_id,
                            expected_attempt,
                            getattr(latest, "attempt", None),
                        )
                        update_heartbeat(task_name, stage="cancelled", progress="旧提取任务失败结果已忽略")
                        return
                project = await db.get(Project, project_id)
                if project:
                    project.status = ProjectStatus.FAILED
                    project.error_message = f"约束提取失败: {str(e)[:500]}"
                    await db.commit()
                    # 预占释放(P1/H6): 约束提取失败时同步释放计费预占
                    try:
                        from app.billing.services.billing_guard import BillingGuard
                        await BillingGuard.on_failed(db, project)
                        await db.commit()
                    except Exception as billing_err:
                        logger.warning(
                            f"约束提取失败释放预占异常(不影响失败状态): "
                            f"project={project_id}, err={billing_err}"
                        )
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
                await RetryService(db).mark_task_failed(
                    project_id,
                    node_key=RetryService.NODE_WRITING_EXTRACT,
                    stage="failed",
                    error=str(e),
                )
            except Exception as retry_err:
                logger.warning(f"更新约束提取 TaskRun 失败态异常(忽略): {retry_err}")
        finally:
            from app.core.writer.extract_prefetch import cancel_extract_prefetch

            await cancel_extract_prefetch(prefetch_tasks)
