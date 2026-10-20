"""招标要求提取后台任务

在后台执行招标要求提取流程，支持进度查询。

流程：
1. 验证文档状态
2. 从招标文件分块中提取要求（并行LLM调用）
3. 合并去重
4. 保存到数据库

心跳机制：
- 每个关键步骤更新心跳，防止任务静默死亡
- 心跳包含当前阶段和进度信息
"""

import asyncio
from typing import Any, Optional
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.project import Project, ProjectStatus
from app.models.document import Document, DocumentType
from app.core.extractor.tender_extractor import get_tender_extractor
from app.core.observability.task_observation import observe_task, TaskFlow
from app.core.observability.milestone import milestone
# 步骤/阶段契约的唯一同源(与状态接口 steps[]、前端步骤映射共用)。
# normalize_extract_heartbeat_stage 在此再导出, 保持既有调用方 import 路径不变。
from app.core.tasks.extract_progress import (  # noqa: F401
    EXTRACT_STAGE_WEIGHTS,
    fold_extract_stage_progress,
    normalize_extract_heartbeat_stage,
)
from app.core.tasks.extract_progress_generation import ExtractProgressGeneration

# 心跳 stage 字面量, 取自契约以避免与 EXTRACT_STAGE_WEIGHTS 漂移。
# mandatory_coverage 阶段由 TenderExtractor 在合并前经 progress_callback 发射。
_STAGE_SCORING_EXTRACT = "scoring_extract"
_STAGE_FINALIZING = "finalizing"


def get_extract_task_name(project_id: int) -> str:
    """获取提取任务名称"""
    return f"extract_project_{project_id}"


def build_extract_dispatch_args(
    project_id: int,
) -> tuple[str, ExtractProgressGeneration, tuple[Any, ...]]:
    """创建提取代际并返回 (task_name, generation, arq_args)。

    职责: 为所有提取入口提供统一的代际创建与 ARQ 参数打包。
    不做: 不分派任务、不写业务心跳、不访问数据库。
    """
    from app.core.tasks.extract_progress_generation import begin_extract_generation

    task_name = get_extract_task_name(project_id)
    generation = begin_extract_generation(task_name)
    arq_args = (project_id, generation.run_id, generation.generation)
    return task_name, generation, arq_args


async def _mark_extract_task_failed(
    db: AsyncSession,
    *,
    project_id: int,
    task_name: str,
    error_message: str,
) -> None:
    """把招标要求提取任务收敛到 FAILED 终态，并同步显式重试 TaskRun。"""
    project = await _get_project(db, project_id)
    if not project:
        return

    project.status = ProjectStatus.FAILED
    project.error_message = error_message[:1000]
    await db.commit()

    # 提取入口已建 hold，失败终态必须幂等释放（契约）。
    try:
        from app.billing.services.billing_guard import BillingGuard
        await BillingGuard.on_failed(db, project)
        await db.commit()
    except Exception as billing_err:
        logger.warning(f"[要求提取] 计费释放异常(忽略): {billing_err}")

    try:
        from app.models.task_run import TaskRunStatus
        from app.services.flow_retry_service import mark_review_task_run_by_task_name

        await mark_review_task_run_by_task_name(
            db,
            project_id=project_id,
            task_name=task_name,
            status=TaskRunStatus.failed.value,
            stage="failed",
            last_error=error_message,
        )
    except Exception as mark_err:
        logger.debug(f"要求提取 TaskRun 失败态回写失败(忽略): {mark_err}")


def _format_item_validation_warning_details(items: list[dict], limit: int = 5) -> str:
    """把被丢弃条目摘要格式化为用户可核对的明细。"""
    if not items:
        return ""

    lines: list[str] = []
    for item in items[:limit]:
        page_num = item.get("page_num")
        page_label = f"第 {page_num} 页" if page_num else "来源页未知"
        summary = str(item.get("summary") or "未能识别的条目").strip()
        reason = str(item.get("reason") or "字段不完整").strip()
        lines.append(f"- {page_label}：{summary}（{reason}）")

    remaining = len(items) - limit
    if remaining > 0:
        lines.append(f"- 另有 {remaining} 条同类内容，请结合原文继续核对")
    return "\n".join(lines)


@milestone(event_key_prefix="extract", task_type="extract", summary_on_success="招标要求提取完成")
@observe_task(flow=TaskFlow.TENDER, task_type="extract")
async def run_extract_task(
    project_id: int,
    generation: Optional[ExtractProgressGeneration] = None,
    *,
    run_id: Optional[str] = None,
    generation_number: Optional[int] = None,
) -> None:
    """执行招标要求提取后台任务
    
    流程：
    1. 验证招标文档存在且已解析
    2. 调用 TenderExtractor 提取招标要求
    3. 更新进度心跳
    
    Args:
        project_id: 项目ID
        generation: 提取代际身份（优先）
        run_id / generation_number: ARQ 位置参数兼容拆分
    """
    # 延迟导入避免循环依赖
    from app.core.tasks import update_heartbeat
    from app.core.arq_runtime import ProjectCancelledError, is_project_cancelled
    from app.core.tasks.cancellation import make_project_cancel_check
    from app.core.tasks.extract_progress_generation import (
        ExtractProgressGeneration as _ExtractProgressGeneration,
        bind_extract_generation,
    )
    
    task_name = get_extract_task_name(project_id)
    resolved = generation
    if resolved is None and run_id is not None and generation_number is not None:
        resolved = _ExtractProgressGeneration(
            run_id=str(run_id),
            generation=int(generation_number),
        )
    if resolved is not None:
        bind_extract_generation(task_name, resolved)
    
    logger.info(f"[后台任务] ========== 开始提取招标要求 project_id={project_id} ==========")
    update_heartbeat(task_name, stage="启动", progress="初始化中")
    if is_project_cancelled(project_id):
        logger.info(f"[提取任务] 项目已取消/删除, 跳过执行: project_id={project_id}")
        update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
        return
    cancel_check = make_project_cancel_check(project_id)
    
    # 创建独立的数据库会话
    async with async_session_factory() as db:
        try:
            cancel_check()
            # 验证项目和文档
            project = await _get_project(db, project_id)
            cancel_check()
            if not project:
                logger.error(f"[后台任务] 项目不存在: {project_id}")
                update_heartbeat(task_name, stage="失败", progress="项目不存在")
                return
            
            tender_doc = await _get_tender_document(db, project_id)
            cancel_check()
            if not tender_doc:
                logger.error(f"[后台任务] 招标文档不存在或未解析: project_id={project_id}")
                update_heartbeat(task_name, stage="失败", progress="招标文档不存在")
                # 走统一失败漏斗，释放提取入口创建的 hold
                await _mark_extract_task_failed(
                    db,
                    project_id=project_id,
                    task_name=task_name,
                    error_message="招标文档不存在或未解析",
                )
                return
            
            _last_log_pct = [0]
            _latest_hb = ["extracting", "0%|开始提取"]

            def progress_callback(
                stage: str,
                current: int,
                total: int,
                message: str,
                extra: Optional[dict] = None,
            ) -> None:
                """进度回调，更新心跳"""
                overall_progress = fold_extract_stage_progress(stage, current, total)
                heartbeat_stage, heartbeat_progress = normalize_extract_heartbeat_stage(
                    stage,
                    overall_progress,
                )
                _latest_hb[0] = heartbeat_stage
                _latest_hb[1] = f"{heartbeat_progress}%|{message}"

                update_heartbeat(
                    task_name,
                    stage=heartbeat_stage,
                    progress=_latest_hb[1],
                    extra=extra or None,
                )

                pct_bucket = int(overall_progress) // 20 * 20
                if pct_bucket > _last_log_pct[0]:
                    _last_log_pct[0] = pct_bucket
                    logger.info(f"[后台任务] 提取进度: {int(overall_progress)}% - {message}")
            
            # 执行提取
            cancel_check()
            update_heartbeat(task_name, stage="extracting", progress="0%|开始提取")
            
            from app.core.config import get_review_concurrency
            extractor = get_tender_extractor(
                db=db,
                concurrency=get_review_concurrency(),
                progress_callback=progress_callback
            )
            
            from app.core.tasks.heartbeat_keepalive import await_with_task_keepalive

            requirements = await await_with_task_keepalive(
                extractor.extract_from_document(
                    document_id=tender_doc.id,
                    save_to_db=True,
                    cancel_check=cancel_check,
                ),
                task_name=task_name,
                stage=lambda: _latest_hb[0],
                progress=lambda: _latest_hb[1],
            )
            cancel_check()
            
            logger.info(f"[后台任务] 招标要求提取完成: {len(requirements)} 条")

            # 高风险原子条件召回已在 TenderExtractor 合并前完成（, 独立步骤
            # mandatory_coverage 由 extractor 进度回调发射）；此处只收集告警。
            _mandatory_warnings: list[str] = []
            coverage_report = getattr(extractor, "_mandatory_coverage_report", None)
            if coverage_report is not None:
                _mandatory_warnings.extend(coverage_report.warnings)
                if coverage_report.inserted_count or coverage_report.promoted_count:
                    logger.info(
                        "[后台任务] 高风险条款补提 {} 条、补标否决 {} 条（已并入合并收敛）",
                        coverage_report.inserted_count,
                        coverage_report.promoted_count,
                    )

            # P2: 评分项提取（评分表聚焦）。独立子模块、开关控制，失败不影响主流程。
            # P0-3a: 收集评分阶段警告，后续写入 project.extraction_warnings
            _scoring_warnings: list[str] = []
            from app.core.config import settings as _scoring_cfg
            if _scoring_cfg.TENDER_SCORING_EXTRACT_ENABLED:
                try:
                    cancel_check()
                    # 评分项提取（含 L3 兜底）可持续数分钟且期间无进度回调，
                    # 刷新心跳文案避免前端停留在"提取完成"却看不到任务仍在推进。
                    # 本阶段是独立的用户可见步骤(scoring_extract)，不再复用 saving。
                    latest = ["90%|评分项提取中"]
                    update_heartbeat(
                        task_name,
                        stage=_STAGE_SCORING_EXTRACT,
                        progress=latest[0],
                    )
                    from app.core.extractor.scoring import ScoringExtractor
                    from app.core.tasks.heartbeat_keepalive import (
                        await_with_task_keepalive,
                    )

                    def scoring_progress(
                        stage: str,
                        current: int,
                        total: int,
                        message: str,
                        extra: Optional[dict] = None,
                    ) -> None:
                        progress_callback(stage, current, total, message, extra)
                        overall = fold_extract_stage_progress(stage, current, total)
                        _heartbeat_stage, heartbeat_progress = (
                            normalize_extract_heartbeat_stage(stage, overall)
                        )
                        latest[0] = f"{heartbeat_progress}%|{message}"

                    scoring_extractor = ScoringExtractor(
                        db=db,
                        project_id=project_id,
                        progress_callback=scoring_progress,
                    )
                    # P0-3a/: 预查 chunk 数量以检测 chunk 存在但落库 0 项的 silent-empty
                    _scoring_chunk_count = len(
                        await scoring_extractor._get_scoring_chunks(tender_doc.id)
                    )
                    # 该阶段内部无进度回调（L2/L3 救援单 scope 预算即 180s），
                    # 只在入口刷一次心跳会让心跳龄越过 600s 判活阈值，进而使状态接口
                    # 把仍在运行的任务误判为已完成。保活使心跳如实反映推进状态。
                    scoring_items = await await_with_task_keepalive(
                        scoring_extractor.extract_from_document(
                            document_id=tender_doc.id, save_to_db=True
                        ),
                        task_name=task_name,
                        stage=_STAGE_SCORING_EXTRACT,
                        progress=lambda: latest[0],
                    )
                    cancel_check()
                    if scoring_items:
                        logger.info(
                            f"[后台任务] 评分项提取完成: {len(scoring_items)} 条"
                        )

                    # 跨通道归并收口——评分条款若已由普通要求(normal)通道提取,
                    # 把 scoring_item 结构化元数据回填 normal 主行并删冗余, 消除双通道重复展示/重复预估。
                    from app.core.extractor.scoring.scoring_reconcile import (
                        reconcile_scoring_items,
                    )

                    # 归并属"归并核验"步骤, 先切阶段再执行, 避免仍显示在评分项提取步内。
                    # 百分数必须落在 finalizing 契约带内(当前 96-100); 禁止沿用旧评分带终点 95。
                    update_heartbeat(
                        task_name,
                        stage=_STAGE_FINALIZING,
                        progress=f"{EXTRACT_STAGE_WEIGHTS[_STAGE_FINALIZING][0]}%|评分条款归并",
                    )

                    reconcile_report = await reconcile_scoring_items(db, tender_doc.id)
                    if reconcile_report.merged_count:
                        logger.info(
                            f"[后台任务] 评分条款跨通道归并: 合并 "
                            f"{reconcile_report.merged_count} 条重复 scoring_item 到 normal 主行"
                        )
                    # P0-3a: 检测 silent-empty（chunk 有但 0 项落库）
                    await db.refresh(tender_doc, ["scoring_verdict_probe"])
                    _probe = getattr(tender_doc, "scoring_verdict_probe", None) or {}
                    _scoring_warnings.extend(
                        _collect_scoring_warnings(
                            scoring_err=None,
                            scoring_chunk_count=_scoring_chunk_count,
                            scoring_item_count=len(scoring_items),
                            presence_absent=(
                                isinstance(_probe, dict)
                                and _probe.get("presence") == "absent"
                            ),
                        )
                    )
                except Exception as scoring_err:
                    logger.warning(
                        f"[后台任务] 评分项提取/归并失败(忽略): {scoring_err}",
                        exc_info=True,
                    )
                    # P0-3a: 异常写入 warnings，不再静默吞掉
                    _scoring_warnings.extend(
                        _collect_scoring_warnings(
                            scoring_err=scoring_err,
                            scoring_chunk_count=0,
                            scoring_item_count=0,
                        )
                    )

            # 评分项开关关闭或该阶段异常时，上面的归并心跳不会发出；此处统一切到
            # "归并核验"步骤，保证质量统计与终态提交阶段有一致的用户可见步骤。
            update_heartbeat(
                task_name,
                stage=_STAGE_FINALIZING,
                progress=f"{EXTRACT_STAGE_WEIGHTS[_STAGE_FINALIZING][0]}%|整理提取结果",
            )

            # 从 extractor 实例提取分块失败统计，持久化到最终心跳
            extraction_warnings: list[str] = []
            chunk_failures = [
                sf for sf in extractor._stage_failures
                if sf["stage"] == "chunk_extraction"
            ]
            item_validation_failures = [
                sf for sf in extractor._stage_failures
                if sf["stage"] == "chunk_item_validation"
            ]

            total_chunks_count = len(await extractor._get_document_chunks(tender_doc.id))
            cancel_check()

            # 去重统计
            seen: set[int] = set()
            unique_failures: list[dict] = []
            for sf in chunk_failures:
                cid = sf.get("chunk_id")
                if cid not in seen:
                    seen.add(cid)
                    unique_failures.append(sf)

            failed_pages = [
                sf.get("page_num")
                for sf in unique_failures
                if sf.get("page_num") is not None
            ]
            failure_rate = (
                len(unique_failures) / total_chunks_count
                if total_chunks_count > 0
                else 0.0
            )

            # 检测连续失败页簇
            max_consecutive = _max_consecutive_run(sorted(set(failed_pages))) if failed_pages else 0

            item_discarded = sum(
                sf.get("failed_count", 0) for sf in item_validation_failures
            )
            item_failure_details: list[dict] = []
            for sf in item_validation_failures:
                failed_items = sf.get("failed_items")
                if not isinstance(failed_items, list):
                    continue
                for item in failed_items:
                    if isinstance(item, dict):
                        item_failure_details.append(item)

            if unique_failures or item_discarded > 0:
                parts: list[str] = []
                if unique_failures:
                    parts.append(
                        f"有 {len(unique_failures)} 个文档分块提取失败"
                        + (f"（涉及第 {', '.join(str(p) for p in failed_pages)} 页）" if failed_pages else "")
                        + "，相关页面的招标要求可能未被提取，建议对照原文手工复核"
                    )
                if item_discarded > 0:
                    detail_text = _format_item_validation_warning_details(
                        item_failure_details
                    )
                    warning = (
                        f"有 {item_discarded} 条疑似要求未能自动整理成列表。"
                        "系统已保留其他有效要求，请人工核对以下位置"
                    )
                    if detail_text:
                        warning += f":\n{detail_text}"
                    else:
                        warning += "。"
                    parts.append(warning)
                extraction_warnings = parts
                logger.warning(f"[后台任务] 提取警告: {extraction_warnings}")

            extraction_quality: dict = {
                "failed_chunk_count": len(unique_failures),
                "failed_pages": failed_pages,
                "failure_rate": round(failure_rate, 4),
                "max_consecutive_fail_pages": max_consecutive,
                "item_salvage_discarded": item_discarded,
                "total_chunks": total_chunks_count,
                "total_extracted": len(requirements),
            }

            from app.core.config import settings as _cfg
            needs_quality_review = (
                failure_rate > _cfg.EXTRACTION_FAILURE_RATE_WARN
                or max_consecutive >= _cfg.EXTRACTION_CONSECUTIVE_FAIL_THRESHOLD
            )
            extraction_quality["needs_quality_review"] = needs_quality_review
            if needs_quality_review:
                extraction_warnings.append(
                    "提取质量异常: "
                    + (f"失败率 {failure_rate:.1%}" if failure_rate > _cfg.EXTRACTION_FAILURE_RATE_WARN else "")
                    + (f", 连续 {max_consecutive} 页失败" if max_consecutive >= _cfg.EXTRACTION_CONSECUTIVE_FAIL_THRESHOLD else "")
                    + "。建议启动审查前先复核失败页对应内容。"
                )

            # P0-3a: 合并评分项提取阶段告警
            extraction_warnings.extend(_scoring_warnings)
            extraction_warnings.extend(_mandatory_warnings)
            cancel_check()
            project.extraction_warnings = (extraction_warnings or None)
            project.status = ProjectStatus.EXTRACTED
            cancel_check()
            await db.commit()
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
                logger.debug(f"要求提取 TaskRun 完成态回写失败(忽略): {mark_err}")

            heartbeat_extra: dict = {}
            if extraction_warnings:
                heartbeat_extra["extraction_warnings"] = extraction_warnings
            heartbeat_extra["extraction_quality"] = extraction_quality

            update_heartbeat(
                task_name,
                stage="completed",
                progress=f"100%|完成，共{len(requirements)}条",
                extra=heartbeat_extra if heartbeat_extra else None,
            )
            from app.services.express_flow.dispatch import queue_express_advance_if_needed
            await queue_express_advance_if_needed(getattr(project, "run_mode", None), project_id)
            
        except ProjectCancelledError:
            logger.info(f"[后台任务] 招标要求提取因项目取消而中止: project_id={project_id}")
            update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
            try:
                await db.rollback()
            except Exception:
                pass
            return
        except asyncio.CancelledError as e:
            if is_project_cancelled(project_id):
                logger.info(f"[后台任务] 招标要求提取取消时项目已取消: project_id={project_id}")
                update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
                try:
                    await db.rollback()
                except Exception:
                    pass
                raise

            error_message = "招标要求提取任务被终止(超时或服务重启)"
            if str(e):
                error_message = f"{error_message}: {str(e)}"
            logger.error(f"[后台任务] 招标要求提取被取消: {error_message}")
            update_heartbeat(task_name, stage="失败", progress=f"错误: {error_message[:50]}")
            try:
                await asyncio.shield(
                    _mark_extract_task_failed(
                        db,
                        project_id=project_id,
                        task_name=task_name,
                        error_message=error_message,
                    )
                )
            except Exception as inner_e:
                logger.error(f"[后台任务] 取消收尾写入失败状态时出错: {inner_e}")
            raise
        except Exception as e:
            if is_project_cancelled(project_id):
                logger.info(f"[后台任务] 招标要求提取异常时项目已取消: project_id={project_id}")
                update_heartbeat(task_name, stage="cancelled", progress="任务已取消")
                try:
                    await db.rollback()
                except Exception:
                    pass
                return
            logger.error(f"[后台任务] 招标要求提取失败: {e}", exc_info=True)
            update_heartbeat(task_name, stage="失败", progress=f"错误: {str(e)[:50]}")
            
            # 更新项目状态
            try:
                await _mark_extract_task_failed(
                    db,
                    project_id=project_id,
                    task_name=task_name,
                    error_message=str(e),
                )
            except Exception as inner_e:
                logger.error(f"[后台任务] 更新失败状态时出错: {inner_e}")


def _collect_scoring_warnings(
    scoring_err: Exception | None,
    scoring_chunk_count: int,
    scoring_item_count: int,
    presence_absent: bool = False,
) -> list[str]:
    """收集评分项提取阶段的警告，供写入 project.extraction_warnings。

    职责: 判定异常/silent-empty/无表门禁 并返回人读友好警告文本列表。
    不做: 不写库、不调用 LLM、不修改任何外部状态。
    """
    if scoring_err is not None:
        return [
            f"评分项提取/归并过程出现异常，请检查评分表是否格式特殊: {scoring_err}"
        ]
    if presence_absent:
        from app.core.extractor.scoring.scoring_table_presence import ABSENT_NOTICE

        return [ABSENT_NOTICE]
    if scoring_chunk_count > 0 and scoring_item_count == 0:
        return [
            f"评分表已识别（{scoring_chunk_count} 个 chunk），"
            "但未能提取到有效评分项，请核对评分表内容。"
        ]
    return []


def _max_consecutive_run(sorted_pages: list[int]) -> int:
    """计算排序后页码列表中最长连续子序列长度。"""
    if not sorted_pages:
        return 0
    max_run = 1
    current_run = 1
    for i in range(1, len(sorted_pages)):
        if sorted_pages[i] == sorted_pages[i - 1] + 1:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1
    return max_run


async def _get_project(db: AsyncSession, project_id: int) -> Optional[Project]:
    """获取项目"""
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    return result.scalar_one_or_none()


async def _get_tender_document(db: AsyncSession, project_id: int) -> Optional[Document]:
    """获取已解析的招标文档"""
    result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.doc_type == DocumentType.TENDER,
            Document.parsed == 1
        )
    )
    return result.scalar_one_or_none()
