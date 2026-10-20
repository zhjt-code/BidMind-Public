"""审查流程管理路由

提供启动审查、查询状态、获取结果、下载报告等 API。
"""

from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, handle_service_error, project_log_context
from app.core.database import get_db
from app.core.exceptions import ServiceError
from app.core.tasks import dispatch_background_job, get_task_heartbeat
from app.core.tasks.extract_task import run_extract_task
from app.core.tasks.process_task import get_process_task_name, run_process_task
from app.core.tasks.review_task import run_review_task
from app.models.project import ProjectStatus
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.review import (
    ReviewResultListResponse,
    ReviewResultResponse,
    ReviewStatus,
    ReviewSummaryResponse,
)
from app.services.flow_retry_service import (
    REVIEW_ARQ_FUNCTION_BY_NODE,
    REVIEW_RETRY_SPECS,
    attach_review_dispatch_result,
    begin_review_retry_attempt,
    build_retry_payload,
    build_review_retry_state,
    cleanup_review_retry_scope,
    infer_review_retry_context,
    is_review_retry_running,
    latest_review_task_run,
    mark_review_task_run_by_task_name,
)
from app.services.review_service import ReviewService

router = APIRouter(
    prefix="/projects/{project_id}",
    dependencies=[Depends(project_log_context)],
)


class ReviewRetryRequest(BaseModel):
    node_key: str | None = None
    target_id: int | None = None


@router.post("/review", response_model=ApiResponse)
async def start_review(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """启动AI审查流程

    前置条件：
    1. 项目必须有招标文档和投标文档
    2. 招标要求必须已提取
    3. 至少有一条已确认的招标要求
    """
    try:
        service = ReviewService(db)
        result = await service.start_review(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="审查任务已启动，正在后台执行",
        data=result,
    )


@router.get("/review/status", response_model=ApiResponse[ReviewStatus])
async def get_review_status(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[ReviewStatus]:
    """获取审查进度状态

    审查流程对外统一为四步：
    1. 投标响应提取
    2. 响应装配校验
    3. AI对比审查
    4. 结果就绪
    """
    try:
        service = ReviewService(db)
        review_status = await service.get_review_status(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(code=200, message="success", data=review_status)


@router.get("/review/retry-state", response_model=ApiResponse)
async def get_review_retry_state(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """查询投标审查项目级失败恢复状态。"""
    try:
        service = ReviewService(db)
        project = await service._get_project_or_raise(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    payload = await build_review_retry_state(db, project)
    return ApiResponse(code=200, message="success", data=payload)


@router.post("/review/retry", response_model=ApiResponse)
async def retry_review(
    project_id: int,
    body: ReviewRetryRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """从项目级失败态重新发起投标审查。"""
    try:
        service = ReviewService(db)
        project = await service._get_project_or_raise(project_id, current_user)
        if project.status == ProjectStatus.REVIEWING:
            raise ServiceError("项目正在审查中，请勿重复启动")
        inferred = await infer_review_retry_context(db, project_id)
        node_key = (body.node_key if body else None) or inferred.node_key
        spec = REVIEW_RETRY_SPECS.get(node_key, REVIEW_RETRY_SPECS["review_project"])
        target_id = (body.target_id if body else None) or (
            inferred.target_id if inferred.node_key == spec.node_key else None
        )

        latest = await latest_review_task_run(db, project_id, spec.node_key)
        if is_review_retry_running(latest):
            payload = await build_review_retry_state(
                db,
                project,
                context=inferred if inferred.node_key == spec.node_key else None,
            )
            payload.update({
                "task_name": latest.task_name,
                "queue_mode": "active",
                "arq_job_id": latest.current_arq_job_id,
            })
            return ApiResponse(code=200, message="审查重试已在运行", data=payload)

        # 首次任务没有 retry_requested_at，is_review_retry_running
        # 为假。心跳误杀后的显式重试必须先证伪 ARQ 在途，禁止再入队。
        from app.core.tasks.lifecycle.liveness_evidence import is_arq_job_possibly_inflight

        inflight_task_name = (
            latest.task_name
            if latest and latest.task_name
            else f"review_project_{project_id}"
        )
        inflight_arq_fn = REVIEW_ARQ_FUNCTION_BY_NODE.get(spec.node_key)
        if inflight_arq_fn and await is_arq_job_possibly_inflight(
            get_task_heartbeat(inflight_task_name),
            task_name=inflight_task_name,
            arq_function=inflight_arq_fn,
        ):
            if project.status == ProjectStatus.FAILED:
                if spec.node_key == "document_process":
                    project.status = ProjectStatus.PARSING
                elif spec.node_key == "requirement_extract":
                    project.status = ProjectStatus.EXTRACTING
                else:
                    project.status = ProjectStatus.REVIEWING
                await db.commit()
            payload = await build_review_retry_state(
                db,
                project,
                context=inferred if inferred.node_key == spec.node_key else None,
            )
            payload.update({
                "task_name": inflight_task_name,
                "queue_mode": "active",
                "arq_job_id": latest.current_arq_job_id if latest else None,
            })
            return ApiResponse(code=200, message="审查重试已在运行", data=payload)

        if project.status != ProjectStatus.FAILED:
            raise ServiceError("仅失败状态支持显式重试", status_code=400)

        # 失败时 on_failed 已释放 hold，显式重试必须与首次启动共享预检，
        # 否则重跑成功后 on_completed 因无 HOLDING 静默跳过，整段零结算。
        # 报告节点为纯 CPU 渲染恢复，与串标 /retry 口径一致地豁免。
        if spec.node_key != "review_report":
            from app.billing.services.balance_gate_error import (
                raise_insufficient_balance,
            )
            from app.billing.services.billing_guard import BillingGuard
            from app.billing.services.wallet_service import InsufficientBalanceError
            try:
                await BillingGuard.pre_check(db, project, project.owner_id)
            except InsufficientBalanceError as balance_err:
                raise_insufficient_balance(balance_err)

        await cleanup_review_retry_scope(db, project_id, spec.node_key, target_id=target_id)
        if spec.node_key == "document_process":
            project.status = ProjectStatus.PARSING
        elif spec.node_key == "requirement_extract":
            project.status = ProjectStatus.EXTRACTING
        elif spec.node_key == "review_report":
            project.status = ProjectStatus.COMPLETED
        else:
            project.status = ProjectStatus.REVIEWING
        project.error_message = None
        await db.commit()

        if spec.node_key == "review_report":
            task_name = f"review_report_{project_id}"
            run = await begin_review_retry_attempt(
                db,
                project_id=project_id,
                node_key=spec.node_key,
                task_name=task_name,
                user_id=current_user.id,
                target_id=target_id,
                last_stage="completed",
            )
            await mark_review_task_run_by_task_name(
                db,
                project_id=project_id,
                task_name=task_name,
                status="completed",
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
            return ApiResponse(code=200, message="报告重试状态已恢复", data=payload)

        if spec.node_key == "document_process":
            if target_id is None:
                raise ServiceError("文档处理重试需要 target_id", status_code=400)
            task_name = get_process_task_name(target_id)
            arq_function = "process_document_job"
            def fallback_factory():
                return run_process_task(project_id, target_id)
            task_type = "process"
            arq_args = (project_id, target_id)
        elif spec.node_key == "requirement_extract":
            from app.core.tasks.extract_task import build_extract_dispatch_args

            task_name, generation, arq_args = build_extract_dispatch_args(project_id)
            arq_function = "extract_requirements_job"
            def fallback_factory():
                return run_extract_task(project_id, generation=generation)
            task_type = "extract"
        else:
            task_name = f"review_project_{project_id}"
            arq_function = "review_project_job"
            def fallback_factory():
                return run_review_task(project_id)
            task_type = "review"
            arq_args = (project_id,)

        run = await begin_review_retry_attempt(
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
        await attach_review_dispatch_result(
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
        return ApiResponse(code=200, message="审查重试已启动", data=payload)
    except ServiceError as e:
        handle_service_error(e)


@router.get("/results", response_model=ReviewResultListResponse)
async def get_review_results(
    project_id: int,
    include_debug: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ReviewResultListResponse:
    """获取审查结果列表"""
    try:
        service = ReviewService(db)
        data = await service.get_review_results(project_id, current_user, include_debug)
    except ServiceError as e:
        handle_service_error(e)

    result_responses = [ReviewResultResponse(**item) for item in data["results"]]
    high_risk_responses = [ReviewResultResponse(**item) for item in data["high_risk_items"]]

    return ReviewResultListResponse(
        summary=ReviewSummaryResponse(**data["summary"]),
        results=result_responses,
        high_risk_items=high_risk_responses,
    )


@router.get("/results/debug", response_model=ReviewResultListResponse)
async def get_review_results_debug(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewResultListResponse:
    """获取包含一致性技术字段的调试视图结果。"""
    return await get_review_results(
        project_id=project_id,
        include_debug=True,
        current_user=current_user,
        db=db,
    )


@router.get("/report/download")
async def download_report(
    project_id: int,
    include_debug: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    """下载PDF审查报告"""
    try:
        service = ReviewService(db)
        pdf_bytes, filename = await service.generate_report(
            project_id, current_user, include_debug,
        )
    except ServiceError as e:
        handle_service_error(e)
    except RuntimeError as e:
        logger.error(f"PDF生成失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"PDF生成异常: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF生成失败: {str(e)}",
        )

    encoded_filename = quote(filename)
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@router.post("/results/reconcile", response_model=ApiResponse)
async def reconcile_review_results(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """对历史审查结果执行一致性重算。"""
    try:
        service = ReviewService(db)
        result = await service.reconcile(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(code=200, message="一致性重算完成", data=result)
