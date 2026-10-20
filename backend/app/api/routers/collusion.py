"""串标审查 API 路由

端点：
  POST   /api/projects/{id}/collusion/analyze          启动串标分析
  GET    /api/projects/{id}/collusion/status            获取分析进度
  GET    /api/projects/{id}/collusion/results           获取分析结果
  GET    /api/projects/{id}/collusion/report/download   下载 PDF 报告

设计对齐 reviews.py：
  - 相同的后台任务启动模式
  - 相同的心跳超时检测 + 自动恢复机制
  - 相同的 DB 状态计数驱动进度计算
"""

from io import BytesIO
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import quote

if TYPE_CHECKING:
    from app.billing.services.wallet_service import InsufficientBalanceError

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, project_log_context
from app.core.config import settings
from app.core.database import get_db
from app.core.filename import build_export_filename
from app.core.tasks import (
    dispatch_background_job,
    get_collusion_task_name,
    get_process_task_name,
    is_task_active,
    run_collusion_task,
    run_process_task,
)
from app.models.collusion_extraction import CollusionExtraction
from app.models.collusion_group import CollusionGroup
from app.models.collusion_result import CollusionResult
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus, ProjectType
from app.models.user import User
from app.schemas.collusion import (
    CollusionProjectSummary,
    CollusionResults,
    CollusionStatus,
    CoverageStats,
    SuspectedGroup,
)
from app.schemas.common import ApiResponse
from app.services.collusion_readiness_service import (
    assess_collusion_document_readiness,
    assess_collusion_readiness,
)
from app.services.collusion_status_service import (
    build_collusion_results as _status_build_collusion_results,
)
from app.services.collusion_status_service import (
    collusion_retry_payload as _status_collusion_retry_payload,
)
from app.services.collusion_status_service import (
    compute_collusion_status as _status_compute_collusion_status,
)
from app.services.collusion_status_service import (
    fractional_progress_from_heartbeat as _status_fractional_progress_from_heartbeat,
)
from app.services.collusion_status_service import (
    get_project_or_404 as _status_get_project_or_404,
)
from app.services.collusion_status_service import (
    group_fractional_progress_from_heartbeat as _status_group_fractional_progress_from_heartbeat,
)
from app.services.collusion_status_service import (
    heartbeat_message as _status_heartbeat_message,
)
from app.services.collusion_status_service import (
    heartbeat_stage_pct as _status_heartbeat_stage_pct,
)
from app.services.collusion_status_service import (
    recheck_project_terminal as _status_recheck_project_terminal,
)
from app.services.collusion_summary_service import (
    RISK_RANK as _SUMMARY_RISK_RANK,
)
from app.services.collusion_summary_service import (
    build_narrative_summary as _summary_build_narrative_summary,
)
from app.services.collusion_summary_service import (
    derive_display_overall_risk as _summary_derive_display_overall_risk,
)
from app.services.collusion_summary_service import (
    derive_overall_risk as _summary_derive_overall_risk,
)
from app.services.collusion_summary_service import (
    derive_project_decision as _summary_derive_project_decision,
)
from app.services.collusion_summary_service import (
    highest_risk as _summary_highest_risk,
)
from app.services.collusion_summary_service import (
    load_or_build_project_summary as _summary_load_or_build_project_summary,
)
from app.services.collusion_summary_service import (
    normalize_suspected_groups as _summary_normalize_suspected_groups,
)
from app.services.collusion_view_service import (
    EMPTY_PARENS_RE as _VIEW_EMPTY_PARENS_RE,
)
from app.services.collusion_view_service import (
    PAIR_REF_PARAM_RE as _VIEW_PAIR_REF_PARAM_RE,
)
from app.services.collusion_view_service import (
    build_evidences_from_legacy as _view_build_evidences_from_legacy,
)
from app.services.collusion_view_service import (
    build_finding_items as _view_build_finding_items,
)
from app.services.collusion_view_service import (
    build_pair_ref_labels as _view_build_pair_ref_labels,
)
from app.services.collusion_view_service import (
    normalize_pair_ref as _view_normalize_pair_ref,
)
from app.services.collusion_view_service import (
    replace_collusion_pair_refs as _view_replace_collusion_pair_refs,
)
from app.services.collusion_view_service import (
    sanitize_collusion_results as _view_sanitize_collusion_results,
)
from app.services.collusion_view_service import (
    step_status as _view_step_status,
)
from app.services.collusion_view_service import (
    strip_pdf_suffix as _view_strip_pdf_suffix,
)
from app.services.flow_retry_service import (
    COLLUSION_RETRY_SPECS,
    attach_collusion_dispatch_result,
    begin_collusion_retry_attempt,
    build_collusion_retry_state,
    build_retry_payload,
    cleanup_collusion_retry_scope,
    infer_collusion_retry_context,
    is_collusion_retry_running,
    latest_collusion_task_run,
    mark_collusion_task_run_by_task_name,
)


def _raise_http_insufficient_balance(exc: "InsufficientBalanceError") -> NoReturn:
    """余额不足转结构化 403，经共享转换器与统一 HTTP 翻译层"""
    from app.api.dependencies import handle_service_error
    from app.billing.services.balance_gate_error import raise_insufficient_balance
    from app.core.exceptions import ServiceError

    try:
        raise_insufficient_balance(exc)
    except ServiceError as service_error:
        handle_service_error(service_error)


router = APIRouter(
    prefix="/projects/{project_id}/collusion",
    dependencies=[Depends(project_log_context)],
)


class CollusionRetryRequest(BaseModel):
    node_key: str | None = None
    target_id: int | None = None


async def _get_project_or_404(
    project_id: int,
    db: AsyncSession,
    current_user: User | None = None,
    *,
    require_owner: bool = False,
) -> Project:
    """thin forwarder → app.services.collusion_status_service.get_project_or_404"""
    return await _status_get_project_or_404(
        project_id, db, current_user, require_owner=require_owner,
    )


async def _recheck_project_terminal(
    db: AsyncSession,
    project_id: int,
) -> tuple[ProjectStatus, str | None] | None:
    """thin forwarder → app.services.collusion_status_service.recheck_project_terminal

 恢复写入前重读项目终态, 避免 worker 收尾与 status 轮询竞态重投（防御）。
 """
    return await _status_recheck_project_terminal(db, project_id)


def _heartbeat_stage_pct(heartbeat: dict | None) -> int:
    """thin forwarder → app.services.collusion_status_service.heartbeat_stage_pct"""
    return _status_heartbeat_stage_pct(heartbeat)


def _heartbeat_message(heartbeat: dict | None, fallback: str | None = None) -> str | None:
    """thin forwarder → app.services.collusion_status_service.heartbeat_message"""
    return _status_heartbeat_message(heartbeat, fallback)


def _fractional_progress_from_heartbeat(
    completed_count: int,
    total_count: int,
    heartbeat: dict | None,
) -> int:
    """thin forwarder → app.services.collusion_status_service.fractional_progress_from_heartbeat"""
    return _status_fractional_progress_from_heartbeat(completed_count, total_count, heartbeat)


def _group_fractional_progress_from_heartbeat(
    heartbeat: dict | None,
    fallback_progress: int,
) -> int:
    """thin forwarder → app.services.collusion_status_service.group_fractional_progress_from_heartbeat"""
    return _status_group_fractional_progress_from_heartbeat(heartbeat, fallback_progress)


# ── C3.1a：启动串标分析 ────────────────────────────────────────────────────────

@router.post("/analyze", response_model=ApiResponse)
async def start_collusion_analyze(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """启动串标分析

    前置条件：
    1. 项目类型必须是 COLLUSION_CHECK
    2. 至少有 2 份已处理完成的投标文件
    3. 项目当前不在分析中
    """
    project = await _get_project_or_404(project_id, db, current_user)

    # 类型校验
    if project.project_type != ProjectType.COLLUSION_CHECK:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该项目不是串标审查类型，请使用投标审查接口"
        )

    # 防止重复启动：与 /retry 对齐改为幂等返回，避免前端 StrictMode/effect 重入
    # 导致的"用户首次启动即弹 400"误报（同源回归）
    if project.status == ProjectStatus.REVIEWING:
        task_name = get_collusion_task_name(project_id)
        if is_task_active(task_name):
            return ApiResponse(
                code=200,
                message="串标分析已在进行中",
                data={
                    "project_id": project_id,
                    "status": "processing",
                    "queue_mode": "active",
                },
            )

    # 检查投标文件数量及解析状态
    bid_result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.doc_type == DocumentType.BID,
        )
    )
    bid_docs = bid_result.scalars().all()

    if len(bid_docs) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"串标分析至少需要 2 份投标文件，当前只有 {len(bid_docs)} 份"
        )

    readiness = await assess_collusion_readiness(db, project_id)
    if not readiness.ready:
        blocking_docs = readiness.blocking_documents
        reupload_docs = [d for d in blocking_docs if d.retry_action == "reupload"]
        reprocess_docs = [d for d in blocking_docs if d.retry_action != "reupload"]
        if reupload_docs:
            names = "、".join(d.filename for d in reupload_docs[:3])
            suffix = "等" if len(reupload_docs) > 3 else ""
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"有 {len(reupload_docs)} 份投标文件源文件不可访问或已损坏："
                    f"{names}{suffix}。请重新上传后再启动串标分析"
                ),
            )
        names = "、".join(d.filename for d in reprocess_docs[:3])
        suffix = "等" if len(reprocess_docs) > 3 else ""
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"有 {len(reprocess_docs)} 份投标文件尚未生成有效分块或向量："
                f"{names}{suffix}。请重新解析/向量化后再启动串标分析"
            ),
        )

    if len(bid_docs) > settings.COLLUSION_MAX_BID_DOCS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"投标文件超出限制（最多 {settings.COLLUSION_MAX_BID_DOCS} 份）"
        )

    # 清除旧对比结果（对比依赖完整提取数据，必须全部重算）
    await db.execute(
        delete(CollusionResult).where(CollusionResult.project_id == project_id)
    )
    # 仅清除失败/空的提取记录，保留成功记录以支持断点续跑
    await db.execute(
        delete(CollusionExtraction).where(
            CollusionExtraction.project_id == project_id,
            CollusionExtraction.extraction_status != "success",
        )
    )
    # 清除旧群体记录
    await db.execute(
        delete(CollusionGroup).where(CollusionGroup.project_id == project_id)
    )

    # 计费预检
    from app.billing.services.billing_guard import BillingGuard
    from app.billing.services.wallet_service import InsufficientBalanceError
    try:
        await BillingGuard.pre_check(db, project, project.owner_id)
    except InsufficientBalanceError as e:
        _raise_http_insufficient_balance(e)

    project.status = ProjectStatus.REVIEWING
    project.error_message = None
    project.collusion_summary = None
    await db.commit()

    task_name = get_collusion_task_name(project_id)

    queue_mode, arq_job_id = await dispatch_background_job(
        task_name=task_name,
        arq_function="collusion_analyze_job",
        fallback_factory=lambda: run_collusion_task(project_id),
        task_type="collusion",
        project_id=project_id,
        arq_args=(project_id,),
    )

    logger.info(
        f"启动串标分析: project_id={project_id}, bid_docs={len(bid_docs)}, "
        f"queue_mode={queue_mode}, arq_job_id={arq_job_id}"
    )

    return ApiResponse(
        code=200,
        message="串标分析任务已启动",
        data={
            "project_id": project_id,
            "bid_doc_count": len(bid_docs),
            "total_pairs": len(bid_docs) * (len(bid_docs) - 1) // 2,
            "status": "processing",
            "queue_mode": queue_mode,
        }
    )


def _collusion_retry_payload(project_id: int, error_message: str | None = None) -> dict:
    """thin forwarder → app.services.collusion_status_service.collusion_retry_payload"""
    return _status_collusion_retry_payload(project_id, error_message)


@router.get("/retry-state", response_model=ApiResponse)
async def get_collusion_retry_state(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """查询串标审查项目级失败恢复状态。"""
    project = await _get_project_or_404(project_id, db, current_user)
    payload = await build_collusion_retry_state(db, project)
    return ApiResponse(code=200, message="success", data=payload)


@router.post("/retry", response_model=ApiResponse)
async def retry_collusion(
    project_id: int,
    body: CollusionRetryRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """从项目级失败态重新发起串标审查。"""
    project = await _get_project_or_404(project_id, db, current_user)
    if project.project_type != ProjectType.COLLUSION_CHECK:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该项目不是串标审查类型")

    inferred = await infer_collusion_retry_context(db, project)
    requested_node = body.node_key if body else None
    if requested_node and requested_node not in COLLUSION_RETRY_SPECS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的串标重试节点")
    node_key = requested_node or inferred.node_key
    spec = COLLUSION_RETRY_SPECS.get(node_key, COLLUSION_RETRY_SPECS["collusion_analyze"])
    target_id = (body.target_id if body else None) or (
        inferred.target_id if inferred.node_key == spec.node_key else None
    )

    latest = await latest_collusion_task_run(db, project_id, spec.node_key)
    if is_collusion_retry_running(latest):
        payload = await build_collusion_retry_state(
            db,
            project,
            context=inferred if inferred.node_key == spec.node_key else None,
        )
        payload.update({
            "task_name": latest.task_name,
            "queue_mode": "active",
            "arq_job_id": latest.current_arq_job_id,
        })
        return ApiResponse(code=200, message="串标重试已在运行", data=payload)

    if project.status == ProjectStatus.REVIEWING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="串标分析正在进行中，请勿重复启动")
    if project.status != ProjectStatus.FAILED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅失败状态支持显式重试")

    if spec.node_key != "collusion_report":
        from app.billing.services.billing_guard import BillingGuard
        from app.billing.services.wallet_service import InsufficientBalanceError
        try:
            await BillingGuard.pre_check(db, project, project.owner_id)
        except InsufficientBalanceError as e:
            _raise_http_insufficient_balance(e)

    if spec.node_key == "document_process" and target_id is not None:
        doc_readiness = await assess_collusion_document_readiness(db, target_id)
        if doc_readiness and doc_readiness.retry_action == "reupload":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"文件 {doc_readiness.filename} 源文件不可访问或已损坏，"
                    "请重新上传文件后再发起解析"
                ),
            )

    await cleanup_collusion_retry_scope(db, project, spec.node_key, target_id=target_id)
    if spec.node_key == "document_process":
        project.status = ProjectStatus.PARSING
    elif spec.node_key == "collusion_report":
        project.status = ProjectStatus.COMPLETED
    else:
        project.status = ProjectStatus.REVIEWING
    project.error_message = None
    await db.commit()

    if spec.node_key == "collusion_report":
        from app.models.task_run import TaskRunStatus

        task_name = f"collusion_report_{project_id}"
        run = await begin_collusion_retry_attempt(
            db,
            project_id=project_id,
            node_key=spec.node_key,
            task_name=task_name,
            user_id=current_user.id,
            target_id=target_id,
            last_stage="completed",
        )
        await mark_collusion_task_run_by_task_name(
            db,
            project_id=project_id,
            task_name=task_name,
            status=TaskRunStatus.completed.value,
            stage="completed",
            last_error=None,
        )
        payload = build_retry_payload(
            spec,
            retryable=False,
            retry_running=False,
            extra={"last_attempt": run.attempt, "target_id": target_id},
        )
        payload.update({
            "task_name": task_name,
            "queue_mode": "sync",
            "arq_job_id": None,
        })
        return ApiResponse(code=200, message="串标报告重试状态已恢复", data=payload)

    if spec.node_key == "document_process":
        if target_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文档处理重试需要 target_id")
        task_name = get_process_task_name(target_id)
        arq_function = "process_document_job"
        def fallback_factory():
            return run_process_task(project_id, target_id)
        task_type = "process"
        arq_args = (project_id, target_id)
    else:
        task_name = get_collusion_task_name(project_id)
        arq_function = "collusion_analyze_job"
        def fallback_factory():
            return run_collusion_task(project_id)
        task_type = "collusion"
        arq_args = (project_id,)

    run = await begin_collusion_retry_attempt(
        db,
        project_id=project_id,
        node_key=spec.node_key,
        task_name=task_name,
        user_id=current_user.id,
        target_id=target_id,
    )
    queue_mode, arq_job_id = await dispatch_background_job(
        task_name=task_name,
        arq_function=arq_function,
        fallback_factory=fallback_factory,
        task_type=task_type,
        project_id=project_id,
        arq_args=arq_args,
    )
    await attach_collusion_dispatch_result(
        db,
        run,
        queue_mode=queue_mode,
        arq_job_id=arq_job_id,
        last_stage="queued",
    )

    if spec.node_key == "document_process" and target_id is not None:
        from app.core.config import settings
        from app.core.vectorizer.job_state import upsert_vector_job_state
        from app.models.vector_job_state import VectorJobStatus

        await upsert_vector_job_state(
            db,
            project_id=project_id,
            document_id=target_id,
            task_name=task_name,
            status=VectorJobStatus.QUEUED if queue_mode == "arq" else VectorJobStatus.PENDING,
            queue_mode=queue_mode,
            stage="queued",
            progress="0/0",
            chunk_fingerprint_version=str(
                getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")
            ),
            arq_job_id=arq_job_id if queue_mode == "arq" else None,
            bump_enqueued_at=(queue_mode == "arq"),
        )
        await db.commit()

    payload = build_retry_payload(
        spec,
        retryable=False,
        retry_running=True,
        extra={"last_attempt": run.attempt, "target_id": target_id},
    )
    payload.update({
        "failed_node": spec.node_key,
        "retry_running": True,
        "queue_mode": queue_mode,
        "task_name": task_name,
        "arq_job_id": arq_job_id,
    })
    return ApiResponse(code=200, message="串标分析重试已启动", data=payload)


# ── C3.1b：获取分析进度 ────────────────────────────────────────────────────────

@router.get("/status", response_model=ApiResponse[CollusionStatus])
async def get_collusion_status(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[CollusionStatus]:
    """获取串标分析进度（thin wrapper → collusion_status_service.compute_collusion_status）

 实际编排逻辑（SQL 聚合 + 6 步进度 + 心跳超时 + 自动恢复 + 终态二次检查）
 位于 collusion_status_service.compute_collusion_status；router 仅做参数解析 + ApiResponse 封装。
 """
    data = await _status_compute_collusion_status(project_id, current_user, db)
    return ApiResponse(code=200, message="success", data=data)


# ── C3.1c：获取分析结果 ────────────────────────────────────────────────────────

@router.get("/results", response_model=ApiResponse[CollusionResults])
async def get_collusion_results(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[CollusionResults]:
    """获取串标分析结果（thin wrapper → collusion_status_service.build_collusion_results）

    实际拼装逻辑（pair 矩阵 + groups + global_findings + summary 收口 + sanitize 出口闸门）
    位于 collusion_status_service.build_collusion_results；
    F4 契约锚点（_load_or_build_project_summary + _sanitize_collusion_results 调用顺序）
    位于 service 内 summary_load_or_build_project_summary → view_sanitize_collusion_results。
    """
    data = await _status_build_collusion_results(project_id, current_user, db)
    return ApiResponse(code=200, message="success", data=data)


# ── C3.1d：下载 PDF 报告 ───────────────────────────────────────────────────────

@router.get("/report/download")
async def download_collusion_report(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """生成并下载串标审查 PDF 报告（thin wrapper）

    实现路径:
    1. 校验项目存在 + 权限 + 状态 == COMPLETED
    2. 调用 collusion_status_service.build_collusion_results 复用拼装层
    3. 调用 CollusionReportGenerator.generate 渲染 PDF
    """
    project = await _get_project_or_404(project_id, db, current_user)

    if project.status != ProjectStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="串标分析尚未完成，无法生成报告",
        )

    results_data: CollusionResults = await _status_build_collusion_results(
        project_id, current_user, db,
    )

    try:
        from app.core.report.collusion_generator import get_collusion_report_generator
        generator = get_collusion_report_generator()
        pdf_bytes = generator.generate(project, results_data)
    except Exception as e:
        logger.error(f"串标报告生成失败: project_id={project_id}, err={e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"报告生成失败: {str(e)}",
        )

    safe_name = quote(build_export_filename(project.name, "串标审查", "pdf"))
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}"},
    )


# ── 私有辅助 ──────────────────────────────────────────────────────────────────

def _build_finding_items(
    r: CollusionResult,
    doc_a_id: int,
    doc_b_id: int,
    doc_a_name: str,
    doc_b_name: str,
    pair_id: str,
) -> list:
    """thin forwarder → app.services.collusion_view_service.build_finding_items

    优先使用 findings 字段（新格式：结构化列表），若 findings 为空或旧格式，
    则回退到 llm_analysis 中各维度的 findings；has_structured 路径锚点保留供测试 inspect。
    """
    return _view_build_finding_items(
        r, doc_a_id, doc_b_id, doc_a_name, doc_b_name, pair_id,
    )


_PAIR_REF_PARAM_RE = _VIEW_PAIR_REF_PARAM_RE
_EMPTY_PARENS_RE = _VIEW_EMPTY_PARENS_RE


def _strip_pdf_suffix(filename: str) -> str:
    """thin forwarder → app.services.collusion_view_service.strip_pdf_suffix"""
    return _view_strip_pdf_suffix(filename)


def _normalize_pair_ref(pair_ref: str) -> str:
    """thin forwarder → app.services.collusion_view_service.normalize_pair_ref"""
    return _view_normalize_pair_ref(pair_ref)


def _build_pair_ref_labels(results: CollusionResults) -> dict[str, str]:
    """thin forwarder → app.services.collusion_view_service.build_pair_ref_labels"""
    return _view_build_pair_ref_labels(results)


def _replace_collusion_pair_refs(text: str | None, pair_labels: dict[str, str]) -> str:
    """thin forwarder → app.services.collusion_view_service.replace_collusion_pair_refs"""
    return _view_replace_collusion_pair_refs(text, pair_labels)


def _sanitize_collusion_results(results: CollusionResults) -> CollusionResults:
    """thin forwarder → app.services.collusion_view_service.sanitize_collusion_results

 串标结果出口闸门 -- 对所有 LLM 自由文本字段做用户态清洗（A2 阶段， 防御）。
 源码锚点保留 sanitize_user_facing_text / _build_pair_ref_labels 调用链，
 供 test_collusion_router_pipeline_contract.test_F2 验证。
 """
    return _view_sanitize_collusion_results(results)


def _build_evidences_from_legacy(
    quote_a: str | None,
    page_a: str | int | None,
    quote_b: str | None,
    page_b: str | int | None,
    doc_a_id: int,
    doc_b_id: int,
    doc_a_name: str,
    doc_b_name: str,
) -> list[dict]:
    """thin forwarder → app.services.collusion_view_service.build_evidences_from_legacy"""
    return _view_build_evidences_from_legacy(
        quote_a, page_a, quote_b, page_b,
        doc_a_id, doc_b_id, doc_a_name, doc_b_name,
    )


def _normalize_suspected_groups(
    groups: list,
    doc_id_to_name: dict[int, str],
) -> list[SuspectedGroup]:
    """thin forwarder → app.services.collusion_summary_service.normalize_suspected_groups"""
    return _summary_normalize_suspected_groups(groups, doc_id_to_name)


def _derive_runtime_project_decision(
    *,
    pair_results: list[CollusionResult],
    group_records: list[CollusionGroup],
    coverage_stats: CoverageStats,
) -> dict:
    """thin forwarder → app.services.collusion_summary_service.derive_project_decision"""
    return _summary_derive_project_decision(
        pair_results=pair_results,
        group_records=group_records,
        coverage_stats=coverage_stats,
    )


def _build_runtime_narrative_summary(
    *,
    decision_info: dict,
    coverage_stats: CoverageStats,
    has_tender_doc: bool,
) -> str:
    """thin forwarder → app.services.collusion_summary_service.build_narrative_summary"""
    return _summary_build_narrative_summary(
        decision_info=decision_info,
        coverage_stats=coverage_stats,
        has_tender_doc=has_tender_doc,
    )


def _load_or_build_project_summary(
    *,
    project: Project,
    pair_results: list[CollusionResult],
    group_records: list[CollusionGroup],
    coverage_stats: CoverageStats,
    has_tender_doc: bool,
    doc_id_to_name: dict[int, str],
) -> CollusionProjectSummary:
    """thin forwarder → app.services.collusion_summary_service.load_or_build_project_summary

 项目级 summary 单真源收口: narrative_summary 必须由规则裁决器实时生成,
 不沿用 stored 中的 LLM 文案; suspected_groups 从实时 group_records 重建。
 """
    return _summary_load_or_build_project_summary(
        project=project,
        pair_results=pair_results,
        group_records=group_records,
        coverage_stats=coverage_stats,
        has_tender_doc=has_tender_doc,
        doc_id_to_name=doc_id_to_name,
    )


def _step_status(pct: int, overall: str) -> str:
    """thin forwarder → app.services.collusion_view_service.step_status"""
    return _view_step_status(pct, overall)


_RISK_RANK: dict[str, int] = _SUMMARY_RISK_RANK


def _highest_risk(risks: list[str]) -> str:
    """thin forwarder → app.services.collusion_summary_service.highest_risk"""
    return _summary_highest_risk(risks)


def _derive_overall_risk(pair_results, group_records=None) -> str:
    """thin forwarder → app.services.collusion_summary_service.derive_overall_risk

    页面级 overallRisk 取 pair 和 group 两个来源的最高值，
    确保与群体风险显示一致，不出现矛盾。
    """
    return _summary_derive_overall_risk(pair_results, group_records)


def _derive_display_overall_risk(
    pair_results: list[CollusionResult],
    group_records: list[CollusionGroup],
) -> tuple[str, str]:
    """thin forwarder → app.services.collusion_summary_service.derive_display_overall_risk

    统一页面展示口径：有群组时按群组风险，无群组时按文件对风险。
    """
    return _summary_derive_display_overall_risk(pair_results, group_records)
