"""AI审查后台任务

在后台执行完整的AI审查流程。

流程：
1. 验证项目状态和文档
2. 提取投标响应（BidExtractor）
3. 匹配招标要求和投标响应（RequirementMatcher）
4. AI逐项对比审查（ReviewOrchestrator）
5. 更新项目状态

心跳机制：
- 每个关键步骤更新心跳，防止任务静默死亡
- 心跳包含当前阶段和进度信息
"""

import asyncio
from typing import Any, Callable, Optional
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.project import Project, ProjectStatus
from app.models.document import Document, DocumentType
from app.core.extractor.bid_extractor import get_bid_extractor
from app.core.ai.reviewer import get_review_orchestrator
from app.core.matcher.requirement_matcher import get_requirement_matcher
from app.core.observability.task_observation import observe_task, TaskFlow
from app.core.observability.milestone import milestone


def _get_task_name(project_id: int) -> str:
    """获取任务名称"""
    return f"review_project_{project_id}"


@milestone(event_key_prefix="review", task_type="review", summary_on_success="投标审查完成")
@observe_task(flow=TaskFlow.TENDER, task_type="review")
async def run_review_task(project_id: int) -> None:
    """执行AI审查后台任务

    职责: 按步骤编排投标审查, 并处理流程级取消/中断/失败收尾
    不做: 不实现各步骤细节(见 _validate_review_inputs 等步骤函数)、不等待文档质检补算

    完整流程：
    1. 验证项目状态和文档
    2. 提取投标响应（从投标文件中检索并提取对应响应）
    3. 匹配招标要求和投标响应
    4. AI逐项对比审查
    5. 更新项目状态

    Args:
        project_id: 项目ID
    """
    # 延迟导入避免循环依赖
    from app.core.tasks import update_heartbeat
    from app.core.arq_runtime import ProjectCancelledError, is_project_cancelled
    from app.core.tasks.cancellation import make_project_cancel_check

    task_name = _get_task_name(project_id)

    logger.info(f"[后台任务] ========== 开始审查 project_id={project_id} ==========")
    update_heartbeat(task_name, stage="启动", progress="初始化中")

    # 入口检查: 项目已删除直接退出
    if is_project_cancelled(project_id):
        logger.info(f"[审查任务] 项目已取消/删除, 跳过执行: project_id={project_id}")
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return
    cancel_check = make_project_cancel_check(project_id)

    # 主 session 收缩为每步独立短 session, 避免 worker 全程持有 1 个连接
    # 长事务 (历史 教训)。各步骤之间通过标量数据 (project_id) 或
    # ORM 对象的已加载列字段 (matched_pairs) 跨 session 传递; 后续步骤需要 ORM
    # 写入时, 在新 session 中重新加载即可。
    try:
        # ========== 步骤1: 验证项目和文档 ==========
        if not await _validate_review_inputs(project_id, task_name, cancel_check):
            return

        # 文档质检是独立结果域: 当前性保障只挂起不等待, 补算不得挡住步骤2 
        try:
            from app.services.document_qa.ensure_current import detach_document_qa_currency

            detach_document_qa_currency(project_id)
            logger.info(f"[后台任务] 文档质检当前性保障已挂起(不等待): project_id={project_id}")
        except Exception as qa_err:
            logger.warning(f"[后台任务] 文档质检当前性保障挂起失败(忽略): {qa_err}")

        # ========== 步骤2: 提取投标响应（关键步骤！）==========
        if not await _extract_bid_responses(project_id, task_name, cancel_check):
            return

        # ========== 步骤3: 匹配招标要求和投标响应 ==========
        matched_pairs = await _match_review_pairs(project_id, task_name, cancel_check)
        if matched_pairs is None:
            return

        # ========== 步骤4: AI逐项对比审查 ==========
        await _review_matched_pairs(project_id, task_name, matched_pairs, cancel_check)

        # ========== 步骤5: 更新项目状态 + 计费结算 ==========
        await _finalize_review_completed(project_id, task_name, cancel_check)

    except ProjectCancelledError:
        logger.info(
            f"[后台任务] 审查任务因项目取消而中止: project_id={project_id}"
        )
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return
    except asyncio.CancelledError:
        # CancelledError 继承 BaseException，下面的 except Exception 抓不到它，
        # 此前 ARQ 墙钟超时/worker 关闭会让任务静默消失、不留任何痕迹。
        # 只记痕迹不写 FAILED（取消通常可恢复），且必须 re-raise 保持 ARQ 取消语义。
        from app.core.tasks.lifecycle.interrupt_contract import record_task_interrupted

        await record_task_interrupted(
            project_id=project_id,
            task_name=task_name,
            task_label="后台任务",
        )
        raise
    except Exception as e:
        await _handle_review_failure(project_id, task_name, e)


async def _handle_review_failure(project_id: int, task_name: str, exc: Exception) -> None:
    """流程级失败收尾: 已取消按取消收尾, 会话并发错误保持 REVIEWING, 其余写 FAILED。

    职责: 把步骤内未处理的异常收敛为项目终态并释放计费预占
    不做: 不重试、不重投任务、不处理 CancelledError(由编排层记中断并上抛)
    """
    from app.core.arq_runtime import is_project_cancelled
    from app.core.tasks import update_heartbeat

    # 二次检查: 异常时若项目已删除, 按取消路径收尾, 不写 FAILED
    if is_project_cancelled(project_id):
        logger.info(
            f"[后台任务] 异常发生时项目已取消, 按取消路径收尾: "
            f"project_id={project_id}, err={str(exc)[:200]}"
        )
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return

    err_msg = str(exc)
    is_session_error = any(keyword in err_msg for keyword in [
        "provisioning a new connection",
        "another operation is in progress",
        "connection is closed",
        "greenlet_spawn has not been called",
    ])
    if is_session_error:
        logger.warning(
            f"[后台任务] session并发错误(可自动恢复), "
            f"保持REVIEWING状态: project_id={project_id}, error={err_msg[:120]}"
        )
        return

    logger.error(f"[后台任务] 审查失败: {exc}", exc_info=True)
    # 主 session 已收缩为各步局部 session, 失败收尾用独立 session,
    # 不再有外层 db 需要 rollback。
    try:
        async with async_session_factory() as err_db:
            fail_project = await _get_project(err_db, project_id)
            if fail_project is None:
                logger.warning(
                    f"[后台任务] 失败收尾: 项目不存在, 跳过: "
                    f"project_id={project_id}"
                )
                return
            try:
                from app.billing.services.billing_guard import BillingGuard
                await BillingGuard.on_failed(err_db, fail_project)
            except Exception as billing_err:
                logger.warning(
                    f"[后台任务] 计费释放异常: "
                    f"project_id={project_id}, err={billing_err}"
                )
                try:
                    await err_db.rollback()
                except Exception:
                    pass
            await _fail_project(
                err_db, fail_project,
                "系统处理异常，请稍后重试或联系管理员。",
            )
    except Exception as inner_e:
        logger.error(
            f"[后台任务] 更新失败状态时出错: "
            f"project_id={project_id}, err={inner_e}"
        )


async def _validate_review_inputs(
    project_id: int,
    task_name: str,
    cancel_check: Callable[[], None],
) -> bool:
    """步骤1: 校验项目、招标文档与投标信封齐套。

    职责: 读取审查输入并校验, 不满足时把项目写成 FAILED
    不做: 不提取响应、不匹配、不审查
    返回 False 表示本步已收尾, 调用方直接结束。
    """
    from app.core.tasks import update_heartbeat

    cancel_check()
    logger.info(f"[后台任务] 步骤1: 验证项目和文档")
    update_heartbeat(task_name, stage="步骤1", progress="验证项目和文档")

    async with async_session_factory() as db:
        project = await _get_project(db, project_id)
        if not project:
            logger.error(f"[后台任务] 项目不存在: {project_id}")
            return False

        tender_doc = await _get_document(db, project_id, DocumentType.TENDER)
        if not tender_doc:
            await _fail_project(db, project, "缺少招标文档")
            return False

        # 投标信封口径: 兼容整本单文档与分卷多文档(同形态齐套边界)
        from app.core.bid_envelope import load_bid_envelope

        bid_envelope = await load_bid_envelope(db, project_id)
        if not bid_envelope.has_documents:
            await _fail_project(db, project, "缺少投标文档")
            return False
        if not bid_envelope.is_complete:
            await _fail_project(
                db, project,
                "投标文件未齐套（分卷模式需商务标与技术标各一份）",
            )
            return False

        tender_doc_id = tender_doc.id
        bid_document_ids = bid_envelope.document_ids

    logger.info(
        f"[后台任务] 文档验证通过: 招标={tender_doc_id}, 投标={list(bid_document_ids)}"
    )
    return True


async def _extract_bid_responses(
    project_id: int,
    task_name: str,
    cancel_check: Callable[[], None],
) -> bool:
    """步骤2: 从投标文件提取各条招标要求的响应并落库。

    职责: 驱动投标响应提取并写提取心跳, 业务失败时把项目写成 FAILED
    不做: 不匹配、不审查; 项目取消异常原样上抛, 由编排层收尾
    返回 False 表示本步已收尾, 调用方直接结束。
    """
    from app.core.arq_runtime import ProjectCancelledError
    from app.core.config import get_review_concurrency
    from app.core.tasks import update_heartbeat
    from app.core.tasks.parallel_unit_progress import parallel_unit_heartbeat_extra

    cancel_check()
    logger.info(f"[后台任务] 步骤2: 开始提取投标响应")
    update_heartbeat(task_name, stage="步骤2", progress="开始提取投标响应")

    concurrency = get_review_concurrency()
    async with async_session_factory() as db:
        bid_extractor = get_bid_extractor(db, concurrency=concurrency, project_id=project_id)
        try:
            bid_responses = await bid_extractor.extract_for_project(
                project_id=project_id,
                save_to_db=True,
                skip_existing=True,
                heartbeat_callback=lambda done, total, inflight=0: update_heartbeat(
                    task_name,
                    stage="步骤2-提取",
                    progress=f"{done}/{total}",
                    extra=parallel_unit_heartbeat_extra(done, total, inflight),
                ),
                cancel_check=cancel_check,
            )
            logger.info(f"[后台任务] 投标响应提取完成: {len(bid_responses)} 条")
            # 舞台已切到装配：先覆盖上一步满格 extra，再关提取 session
            update_heartbeat(
                task_name,
                stage="步骤3",
                progress="匹配中",
                extra=parallel_unit_heartbeat_extra(0, 0, 0),
            )
        except ValueError as e:
            fail_project = await _get_project(db, project_id)
            if fail_project is not None:
                await _fail_project(db, fail_project, f"投标响应提取失败: {e}")
            return False
        except ProjectCancelledError:
            raise
        except Exception as e:
            logger.error(f"[后台任务] 投标响应提取异常: {e}", exc_info=True)
            # 主 session 可能因前步 SQL 失败进入 aborted, 用独立 session 兜底
            try:
                await db.rollback()
            except Exception:
                pass
            async with async_session_factory() as fail_db:
                fail_project = await _get_project(fail_db, project_id)
                if fail_project is not None:
                    await _fail_project(fail_db, fail_project, f"投标响应提取异常: {e}")
            return False
    return True


async def _match_review_pairs(
    project_id: int,
    task_name: str,
    cancel_check: Callable[[], None],
) -> Optional[list[Any]]:
    """步骤3: 把已确认招标要求与已提取响应装配成审查对。

    职责: 驱动匹配器装配审查对并写装配心跳, 业务失败时把项目写成 FAILED
    不做: 不提取响应、不调用审查模型
    返回 None 表示本步已收尾, 调用方直接结束。
    """
    from app.core.tasks import update_heartbeat
    from app.core.tasks.parallel_unit_progress import parallel_unit_heartbeat_extra

    cancel_check()
    logger.info(f"[后台任务] 步骤3: 匹配招标要求和投标响应")
    update_heartbeat(
        task_name,
        stage="步骤3",
        progress="匹配中",
        extra=parallel_unit_heartbeat_extra(0, 0, 0),
    )

    async with async_session_factory() as db:
        matcher = get_requirement_matcher(db)
        try:
            matched_pairs = await matcher.match_project(
                project_id,
                progress_callback=lambda done, total, message: update_heartbeat(
                    task_name,
                    stage="步骤3-匹配",
                    progress=f"{done}/{total}",
                    extra={
                        "substage": "assemble_pairs",
                        "current": done,
                        "total": total,
                        "stage_progress_pct": int(done / total * 100) if total > 0 else 0,
                        "message": message,
                    },
                ),
                cancel_check=cancel_check,
            )
        except ValueError as e:
            logger.warning(f"[后台任务] 匹配阶段业务异常: {e}")
            fail_project = await _get_project(db, project_id)
            if fail_project is not None:
                await _fail_project(db, fail_project, "未找到已确认的招标要求，请先完成招标要求确认。")
            return None

        # 打印匹配统计
        summary = matcher.summarize_matches(matched_pairs)
        logger.info(
            f"[后台任务] 匹配完成: 总数={summary['total']}, "
            f"有响应={summary['with_response']}, "
            f"无响应={summary['without_response']}"
        )
    # session 退出: matched_pairs 内 ORM 已 detach, 列字段已加载, 可跨 session 只读
    return matched_pairs


async def _review_matched_pairs(
    project_id: int,
    task_name: str,
    matched_pairs: list[Any],
    cancel_check: Callable[[], None],
) -> None:
    """步骤4: AI 逐项对比审查。

    职责: 驱动审查编排器逐项审查并写审查心跳
    不做: 不写项目终态、不结算计费; 异常原样上抛, 由编排层收尾
    """
    from app.core.tasks import update_heartbeat
    from app.core.tasks.parallel_unit_progress import parallel_unit_heartbeat_extra

    cancel_check()
    logger.info(f"[后台任务] 步骤4: 开始AI逐项对比审查 ({len(matched_pairs)} 项)")
    update_heartbeat(
        task_name,
        stage="步骤4",
        progress=f"0/{len(matched_pairs)}",
        extra=parallel_unit_heartbeat_extra(0, len(matched_pairs), 0),
    )

    # 不再包裹外层 async_session_factory; 编排器内部所有 DB 操作均通过
    # db_session_scope() 申请短 session, 外层不再常驻 1 条空闲连接占用 QueuePool。
    # 历史外层 session 经核查仅作为 `self.db` 兜底分支存在, 并发路径从不命中。
    def emit_review_heartbeat(
        done: int,
        total: int,
        inflight: int = 0,
        *,
        stage: str = "步骤4-审查",
        phase: str | None = None,
    ) -> None:
        """审查步骤心跳; 支持 retry 阶段写入 phase=self_healing。"""
        extra = parallel_unit_heartbeat_extra(done, total, inflight)
        if phase:
            extra["phase"] = phase
        update_heartbeat(
            task_name,
            stage=stage,
            progress=f"{done}/{total}",
            extra=extra,
        )

    orchestrator = get_review_orchestrator(db=None, project_id=project_id)
    results = await orchestrator.review_project(
        project_id=project_id,
        matched_pairs=matched_pairs,
        save_to_db=True,
        heartbeat_callback=emit_review_heartbeat,
        cancel_check=cancel_check,
    )

    logger.info(f"[后台任务] AI审查完成: {len(results)} 条结果")


async def _finalize_review_completed(
    project_id: int,
    task_name: str,
    cancel_check: Callable[[], None],
) -> None:
    """步骤5: 写项目完成态、结算计费、释放通道绑定。

    职责: 按「完成态主事实 -> 计费 -> 释放绑定」的顺序收尾成功的审查
    不做: 不处理失败收尾(见 _handle_review_failure)
    """
    from app.core.tasks import update_heartbeat

    # Fix: 将"项目完成态提交"和"计费结算"拆入独立干净 session，
    # 避免同一长事务被计费异常污染后 COMPLETED/FAILED 均无法落库。
    update_heartbeat(task_name, stage="步骤5", progress="更新状态")

    # 步骤 1-4 各自独立 session, 此处无须主 session rollback。
    if not await _commit_review_completed(project_id, task_name, cancel_check):
        return
    await _settle_review_billing(project_id, cancel_check)

    try:
        from app.core.ai import release_project_binding
        await release_project_binding(project_id)
    except Exception:
        pass

    logger.info(f"[后台任务] ========== 项目审查完成 project_id={project_id} ==========")


async def _commit_review_completed(
    project_id: int,
    task_name: str,
    cancel_check: Callable[[], None],
) -> bool:
    """5.1 先在干净 session 中提交项目完成态（主事实源）。

    职责: 写 COMPLETED 主事实并回写 TaskRun, 回写失败只记日志
    不做: 不结算计费
    返回 False 表示项目已不存在, 跳过后续收尾。
    """
    cancel_check()
    async with async_session_factory() as status_db:
        status_project = await _get_project(status_db, project_id)
        cancel_check()
        if status_project is None:
            logger.warning(
                f"[后台任务] 步骤5: 项目不存在(可能被删除), 跳过状态写入: "
                f"project_id={project_id}"
            )
            return False
        status_project.status = ProjectStatus.COMPLETED
        status_project.error_message = None
        cancel_check()
        await status_db.commit()
        try:
            from app.models.task_run import TaskRunStatus
            from app.services.flow_retry_service import mark_review_task_run_by_task_name

            await mark_review_task_run_by_task_name(
                status_db,
                project_id=project_id,
                task_name=task_name,
                status=TaskRunStatus.completed.value,
                stage="completed",
                last_error=None,
            )
        except Exception as mark_err:
            logger.debug(f"审查 TaskRun 完成态回写失败(忽略): {mark_err}")
    return True


async def _settle_review_billing(
    project_id: int,
    cancel_check: Callable[[], None],
) -> None:
    """5.2 再在独立 session 中执行计费结算，失败仅记日志不反噬完成态。

    职责: 结算本次审查的计费预占
    不做: 不改项目状态
    """
    try:
        cancel_check()
        async with async_session_factory() as billing_db:
            billing_project = await _get_project(billing_db, project_id)
            cancel_check()
            if billing_project is None:
                raise RuntimeError("project_missing_during_billing_settle")
            try:
                from app.billing.services.billing_guard import BillingGuard
                await BillingGuard.on_completed(billing_db, billing_project)
                await billing_db.commit()
            except Exception:
                await billing_db.rollback()
                raise
    except Exception as billing_err:
        logger.warning(
            f"[后台任务] 计费结算异常(不影响项目状态): project_id={project_id}, "
            f"err={billing_err}"
        )


async def _get_project(db: AsyncSession, project_id: int) -> Optional[Project]:
    """获取项目"""
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    return result.scalar_one_or_none()


async def _get_document(
    db: AsyncSession,
    project_id: int,
    doc_type: DocumentType
) -> Optional[Document]:
    """获取指定类型的文档"""
    result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.doc_type == doc_type
        )
    )
    return result.scalar_one_or_none()


async def _fail_project(
    db: AsyncSession,
    project: Project,
    reason: str
) -> None:
    """将项目标记为失败"""
    logger.warning(f"[后台任务] 项目失败: {project.id}, 原因: {reason}")
    project.status = ProjectStatus.FAILED
    project.error_message = reason[:1000]
    await db.commit()

    # 预占释放(P1/H6): 项目转 FAILED 时同步释放计费预占,
    # 失败仅记日志不影响主失败流程
    try:
        from app.billing.services.billing_guard import BillingGuard
        async with async_session_factory() as billing_db:
            billing_project = await _get_project(billing_db, project.id)
            if billing_project is not None:
                await BillingGuard.on_failed(billing_db, billing_project)
                await billing_db.commit()
    except Exception as billing_err:
        logger.warning(
            f"[后台任务] 项目失败时计费释放异常(不影响失败状态): "
            f"project={project.id}, err={billing_err}"
        )
    try:
        from app.models.task_run import TaskRunStatus
        from app.services.flow_retry_service import mark_review_task_run_by_task_name

        await mark_review_task_run_by_task_name(
            db,
            project_id=project.id,
            task_name=_get_task_name(project.id),
            status=TaskRunStatus.failed.value,
            stage="failed",
            last_error=reason,
        )
    except Exception as mark_err:
        logger.debug(f"审查 TaskRun 失败态回写失败(忽略): {mark_err}")
    try:
        from app.core.ai import release_project_binding
        await release_project_binding(project.id)
    except Exception:
        pass
