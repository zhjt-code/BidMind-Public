"""文件上传路由

提供 PDF 文件上传、后台处理触发和处理进度查询的 API。
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.database import get_db
from app.core.config import settings
from app.core.convert.office_to_pdf import (
    ALLOWED_UPLOAD_MIME_TYPES,
    ALLOWED_UPLOAD_SUFFIXES,
    UPLOAD_FORMAT_ERROR,
    sniff_upload_magic,
    suffix_of,
)
from app.core.bid_envelope import parse_bid_part
from app.core.rate_limit import limiter
from app.models.document import DocumentType
from app.models.project import Project, ProjectType
from app.models.user import User
from app.schemas.document import (
    DocumentUploadResponse,
    DocumentResponse,
    DocumentListResponse,
)
from app.schemas.common import ApiResponse
from app.services.document_service import DocumentService
from app.services.document_gate_service import DocumentGateService
from app.api.dependencies import get_current_user, check_project_permission, project_log_context
from app.core.redis import get_redis_pool
from app.core.arq_runtime import can_use_arq
from app.core.vectorizer.job_state import (
    enqueue_process_document_job,
    get_vector_job_state,
    upsert_vector_job_state,
)
from app.models.vector_job_state import VectorJobStatus


router = APIRouter(
    prefix="/projects/{project_id}/files",
    dependencies=[Depends(project_log_context)],
)

# 与 nginx/nginx.conf 的 client_max_body_size 保持一致；本地直连 FastAPI 也走此上限。
_MAX_UPLOAD_SIZE_MB = 200


async def _get_project_with_auth(
    project_id: int, current_user: User, db: AsyncSession
) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"项目不存在: id={project_id}")
    check_project_permission(project, current_user)
    return project


@router.post("", response_model=ApiResponse[DocumentUploadResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: settings.RATE_LIMIT_UPLOAD)
async def upload_file(
    request: Request,
    project_id: int,
    doc_type: str = Form(..., description="文档类型: tender 或 bid"),
    bid_part: Optional[str] = Form(
        None,
        description="投标分卷: commercial(商务标) / technical(技术标), 留空=整本",
    ),
    file: UploadFile = File(..., description="PDF 或 Word 文件"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[DocumentUploadResponse]:
    """上传文档"""
    await _get_project_with_auth(project_id, current_user, db)

    # 验证文档类型
    try:
        document_type = DocumentType(doc_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的文档类型: {doc_type}，应为 tender 或 bid"
        )

    # 验证投标分卷角色(留空=整本)
    try:
        envelope_part = parse_bid_part(bid_part)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    
    suffix = suffix_of(file.filename or "")
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=UPLOAD_FORMAT_ERROR,
        )

    if file.content_type and file.content_type not in ALLOWED_UPLOAD_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的文件类型: {file.content_type}，{UPLOAD_FORMAT_ERROR}",
        )

    # 读取文件内容(客户端超时断连时可能抛出异常)
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件读取失败,可能是客户端连接中断: {e}"
        )

    # 文件大小校验（与云端 nginx client_max_body_size 对齐）
    max_file_size = _MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件过大，最大支持 {_MAX_UPLOAD_SIZE_MB}MB"
        )

    if not sniff_upload_magic(content, suffix):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="文件内容与扩展名不符，不是有效的 PDF / Word 文件",
        )
    
    # 保存文件
    service = DocumentService(db)
    
    try:
        document = await service.save_uploaded_file(
            project_id=project_id,
            doc_type=document_type,
            filename=file.filename,
            file_content=content,
            bid_part=envelope_part,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    logger.info(f"文件上传成功: project={project_id}, doc_type={doc_type}, file={file.filename}")

    # 文档门禁: 解析前要素校验(非阻断, fail-open), 结果随上传响应返回供前端弹窗提示
    gate_service = DocumentGateService(db)
    gate_result = await gate_service.evaluate_uploaded_document(document, content)

    # 缺口D: 同项目字节级副本提示(fail-visible, 不阻断——用户可能有意重传)
    upload_message = "上传成功，请调用处理接口开始解析"
    duplicate = await service.find_duplicate_document(
        project_id, document.file_hash or "", exclude_document_id=document.id
    )
    if duplicate is not None:
        upload_message = (
            f"上传成功。注意：该文件与已上传文档《{duplicate.filename}》内容完全相同，请确认是否重复上传"
        )
        logger.warning(
            "文件上传副本提示: project={} document={} 与 document={} 字节指纹相同",
            project_id,
            document.id,
            duplicate.id,
        )

    return ApiResponse(
        code=201,
        message="文件上传成功",
        data=DocumentUploadResponse(
            id=document.id,
            project_id=document.project_id,
            doc_type=document.doc_type,
            bid_part=document.bid_part,
            filename=document.filename,
            file_size=document.file_size,
            message=upload_message,
            gate_result=gate_result,
        )
    )


@router.get("", response_model=ApiResponse[DocumentListResponse])
async def list_project_files(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[DocumentListResponse]:
    """获取项目文档列表"""
    await _get_project_with_auth(project_id, current_user, db)
    service = DocumentService(db)
    docs = await service.get_project_documents(project_id)
    
    return ApiResponse(
        code=200,
        message="success",
        data=DocumentListResponse(
            tender=DocumentResponse.model_validate(docs["tender"]) if docs["tender"] else None,
            bid=DocumentResponse.model_validate(docs["bid"]) if docs["bid"] else None,
            bids=[DocumentResponse.model_validate(d) for d in docs.get("bids", [])],
        )
    )


@router.post("/{document_id}/process", response_model=ApiResponse)
async def process_document(
    project_id: int,
    document_id: int,
    skip_vl: bool = Query(False, description="跳过VL图片识别"),
    gate_ack: bool = Query(False, description="用户已确认门禁警告并选择继续"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """触发文档处理（后台任务）

    立即返回, 处理在后台执行。通过 GET /process/status 轮询进度。
    文档存在性由后台任务自行验证（避免事务隔离导致的竞态问题）。
    """
    project = await _get_project_with_auth(project_id, current_user, db)

    from app.core.tasks import (
        create_background_task,
        is_task_active,
        mark_task_queued,
        update_heartbeat,
    )
    from app.core.tasks.process_task import (
        get_process_task_name,
        resolve_effective_skip_vl,
        run_process_task,
    )

    # 检查是否已有正在运行的任务
    task_name = get_process_task_name(document_id)
    if is_task_active(task_name):
        return ApiResponse(
            code=200,
            message="文档正在处理中",
            data={"status": "processing", "document_id": document_id}
        )

    # 入队前强制绑定子资源归属，避免用有权限项目触发其他项目文档处理。
    service = DocumentService(db)
    document = await service.get_document_by_id_and_project(document_id, project_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在或不属于该项目",
        )
    if project.project_type == ProjectType.BID_REVIEW:
        has_pair = await service.has_bid_review_document_pair(project_id)
        if not has_pair:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="投标审查项目必须同时上传招标文件和投标文件后才能解析",
            )
    if document.parsed == 1:
        return ApiResponse(
            code=200,
            message="文档已处理完成",
            data={"status": "completed", "document_id": document_id}
        )

    # 门禁警告确认留痕(不改变任何流程行为)
    if gate_ack:
        await DocumentGateService(db).acknowledge_gate(document)

    # ARQ 三条件判定: 开关开启 + Redis 可用 + 存在支持该任务的新鲜 worker 心跳
    # 任一不满足则回退 asyncio, 避免 queued 无人消费
    use_arq = await can_use_arq("process_document_job")
    pool = await get_redis_pool() if use_arq else None
    arq_job_id = await enqueue_process_document_job(
        pool,
        project_id=project_id,
        document_id=document_id,
        skip_vl=skip_vl,
    )
    queue_mode = "arq" if arq_job_id is not None else "asyncio_fallback"
    if queue_mode == "asyncio_fallback":
        create_background_task(
            run_process_task(project_id, document_id, skip_vl=skip_vl, queue_mode=queue_mode),
            name=task_name,
            task_type="process",
            project_id=project_id,
            fallback_control=True,
        )
    # 立即写 "queued" 心跳, 防止状态查询在 worker 领取前把任务误判为丢失并重复入队
    effective_skip_vl = resolve_effective_skip_vl(
        project_type=project.project_type,
        document_type=document.doc_type,
        requested_skip_vl=skip_vl,
    )
    mark_task_queued(
        task_name,
        queue_mode=queue_mode,
        arq_job_id=arq_job_id,
        project_id=project_id,
        extra={"vl_skipped": True} if effective_skip_vl else None,
    )

    # ARQ 路径下状态机写入 QUEUED + 入队时间戳; asyncio 路径沿用 PENDING 语义
    initial_status = VectorJobStatus.QUEUED if queue_mode == "arq" else VectorJobStatus.PENDING
    await upsert_vector_job_state(
        db,
        project_id=project_id,
        document_id=document_id,
        task_name=task_name,
        status=initial_status,
        queue_mode=queue_mode,
        stage="queued",
        progress="0/0",
        chunk_fingerprint_version=str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
        arq_job_id=arq_job_id if queue_mode == "arq" else None,
        bump_enqueued_at=(queue_mode == "arq"),
    )
    await db.commit()

    logger.info(
        f"已启动文档处理任务: document_id={document_id}, project_id={project_id}, "
        f"queue_mode={queue_mode}, arq_job_id={arq_job_id}"
    )

    return ApiResponse(
        code=200,
        message="文档处理已启动",
        data={"status": "started", "document_id": document_id}
    )


@router.delete("/{document_id}", response_model=ApiResponse)
async def delete_unparsed_document(
    project_id: int,
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """删除尚未解析的文档

    用于门禁警告后用户选择"返回更换文档"的场景，避免可疑文档残留后
    被状态对账任务自动拉起解析。仅允许删除 parsed=0 且无处理任务在跑的文档。
    """
    await _get_project_with_auth(project_id, current_user, db)

    from app.core.tasks import is_task_active
    from app.core.tasks.process_task import get_process_task_name

    if is_task_active(get_process_task_name(document_id)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档正在处理中，不能删除",
        )

    service = DocumentService(db)
    try:
        payload = await service.delete_unparsed_document(project_id, document_id)
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return ApiResponse(code=200, message="文档已删除", data=payload)


@router.post("/reset-upload", response_model=ApiResponse)
async def reset_bid_review_upload(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """重置投标审查上传半成品。

    仅用于上传/解析阶段异常恢复；审查中或已完成项目不得通过该入口回退。
    """
    await _get_project_with_auth(project_id, current_user, db)
    service = DocumentService(db)
    try:
        payload = await service.reset_bid_review_upload(project_id)
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return ApiResponse(
        code=200,
        message="上传状态已重置，请重新上传招标文件和投标文件",
        data=payload,
    )


@router.get("/{document_id}/process/status", response_model=ApiResponse)
async def get_process_status(
    project_id: int,
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """查询文档处理进度

    返回当前处理阶段和进度信息。前端应每2秒轮询一次。
    如果任务丢失(如服务重启), 会自动重新启动任务。
    """
    await _get_project_with_auth(project_id, current_user, db)
    from app.core.tasks import MAX_RECOVERY, get_task_heartbeat
    from app.core.tasks.process_task import get_process_task_name
    from app.services.liveness.document_processing_reconciler import (
        reconcile_document_processing,
    )

    task_name = get_process_task_name(document_id)

    # 委托给共享 reconciler 完成"任务失活判定 + 二次确认 + 转失败/重入队"决策与副作用。
    # 路由仅做响应组装; 同一份契约由 maintenance reaper 共享, 避免双路径不一致。
    result = await reconcile_document_processing(
        db, project_id=project_id, document_id=document_id
    )

    if result.outcome == "completed":
        stages = _build_stages("completed", "")
        return ApiResponse(
            code=200,
            message="处理完成",
            data={
                "status": "completed",
                "stage": "completed",
                "stage_label": "处理完成",
                "progress": 0,
                "total": 0,
                "stages": stages,
                "steps": stages,
                "overall_status": "completed",
                "overall_progress": 100,
                "message": "处理完成",
            },
        )

    if result.outcome == "already_failed":
        stages = _build_stages("failed", "")
        # already_failed 路径上 reconciler 不再读取 job_state, 这里轻量读一次用于响应字段
        job_state = await get_vector_job_state(db, document_id)
        error_message = result.error_message or "未知错误"
        return ApiResponse(
            code=200,
            message="处理失败",
            data={
                "status": "failed",
                "stage": "failed",
                "stage_label": "处理失败",
                "progress": 0,
                "total": 0,
                "error": error_message,
                "stages": stages,
                "steps": stages,
                "overall_status": "failed",
                "overall_progress": 0,
                "message": error_message,
                "job_status": (job_state.status.value if job_state else None),
                "queue_mode": (job_state.queue_mode if job_state else None),
            },
        )

    if result.outcome == "max_recovery_exceeded":
        return ApiResponse(
            code=200,
            message="处理失败",
            data={
                "status": "failed",
                "stage": "failed",
                "stage_label": "处理失败",
                "progress": 0,
                "total": 0,
                "error": result.error_message
                or f"任务恢复失败(已重试{MAX_RECOVERY}次)，请检查服务器环境后重新上传文件",
                "stages": _build_stages("failed", ""),
            },
        )

    if result.outcome == "recovered":
        from app.core.tasks.heartbeat_codec import is_explicit_true

        recovered_hb = get_task_heartbeat(task_name) or {}
        vl_skipped = is_explicit_true(recovered_hb.get("vl_skipped"))
        stages = _build_stages("parsing", "")
        return ApiResponse(
            code=200,
            message="处理任务已恢复",
            data={
                "status": "processing",
                "stage": "parsing",
                "stage_label": "恢复中...",
                "progress": 0,
                "total": 0,
                "stages": stages,
                "steps": stages,
                "overall_status": "running",
                "overall_progress": 0,
                "message": "恢复中...",
                "vl_skipped": vl_skipped,
            },
        )

    # outcome == "noop": 任务运行健康, 落到正常处理中分支用心跳渲染进度
    from app.core.tasks.heartbeat_codec import is_explicit_true

    heartbeat = get_task_heartbeat(task_name)
    job_state = await get_vector_job_state(db, document_id)

    # 读取 VL 跳过标记
    vl_skipped = is_explicit_true((heartbeat or {}).get("vl_skipped"))

    # 正常处理中, 从心跳获取进度
    stage = heartbeat.get("stage", "parsing") if heartbeat else "parsing"
    progress_str = heartbeat.get("progress", "") if heartbeat else ""
    recognized = int(heartbeat.get("recognized", 0)) if heartbeat else 0
    attempted = int(heartbeat.get("attempted", 0)) if heartbeat else 0
    failed_pending = int(heartbeat.get("failed_pending", 0)) if heartbeat else 0
    retry_round = int(heartbeat.get("retry_round", 0)) if heartbeat else 0
    vl_phase = str(heartbeat.get("vl_phase", "")) if heartbeat else ""
    vl_current_round = int(heartbeat.get("vl_current_round", 0)) if heartbeat else 0
    vl_round_done = int(heartbeat.get("vl_round_done", 0)) if heartbeat else 0
    vl_round_total = int(heartbeat.get("vl_round_total", 0)) if heartbeat else 0
    vl_retry_pending = int(heartbeat.get("vl_retry_pending", 0)) if heartbeat else 0
    vl_unrecognized_final = int(heartbeat.get("vl_unrecognized_final", 0)) if heartbeat else 0
    manual_review_required = bool(heartbeat.get("manual_review_required", False)) if heartbeat else False
    unrecognized_images = int(heartbeat.get("unrecognized_images", 0)) if heartbeat else 0
    unrecognized_pages = int(heartbeat.get("unrecognized_pages", 0)) if heartbeat else 0
    vl_concurrency = int(heartbeat.get("vl_concurrency", 0)) if heartbeat else 0
    vl_p95_ms = int(heartbeat.get("vl_p95_ms", 0)) if heartbeat else 0
    vl_adjust_desc = str(heartbeat.get("vl_adjust_desc", "")) if heartbeat else ""

    vectorizing_substage = str(heartbeat.get("vectorizing_substage", "")) if heartbeat else ""
    vectorizing_meta = heartbeat.get("vectorizing_meta") if heartbeat else None
    degraded_to_cpu = bool(heartbeat.get("degraded_to_cpu", False)) if heartbeat else False
    fallback_reason = str(heartbeat.get("fallback_reason", "")) if heartbeat else ""
    recovery_mode = str(heartbeat.get("recovery_mode", "thread")) if heartbeat else "thread"

    # 解析进度字符串 "23/143"
    progress = 0
    total = 0
    if "/" in progress_str:
        try:
            parts = progress_str.split("/")
            progress = int(parts[0])
            total = int(parts[1])
        except (ValueError, IndexError):
            pass

    stage_label = _get_stage_label(stage)
    stages = _build_stages(stage, progress_str)

    return ApiResponse(
        code=200,
        message="处理中",
        data={
            "status": "processing",
            "stage": stage,
            "stage_label": stage_label,
            "progress": progress,
            "total": total,
            "recognized": recognized,
            "attempted": attempted,
            "failed_pending": failed_pending,
            "retry_round": retry_round,
            "vl_phase": vl_phase,
            "vl_current_round": vl_current_round,
            "vl_round_done": vl_round_done,
            "vl_round_total": vl_round_total,
            "vl_retry_pending": vl_retry_pending,
            "vl_unrecognized_final": vl_unrecognized_final,
            "manual_review_required": manual_review_required,
            "unrecognized_images": unrecognized_images,
            "unrecognized_pages": unrecognized_pages,
            "vl_concurrency": vl_concurrency,
            "vl_p95_ms": vl_p95_ms,
            "vl_adjust_desc": vl_adjust_desc,
            "stages": stages,
            "steps": stages,
            "overall_status": "running",
            "overall_progress": _compute_process_overall_progress(stages),
            "message": stage_label,
            "vl_skipped": vl_skipped,
            "vectorizing_substage": vectorizing_substage,
            "vectorizing_meta": vectorizing_meta,
            "degraded_to_cpu": degraded_to_cpu,
            "fallback_reason": fallback_reason,
            "recovery_mode": recovery_mode,
            # ARQ 状态机精细字段 (前端可选消费; 未接入时老字段兼容)
            "job_status": (job_state.status.value if job_state else None),
            "queue_mode": (
                job_state.queue_mode if job_state else (heartbeat or {}).get("queue_mode")
            ),
            "arq_job_id": (job_state.arq_job_id if job_state else None),
            "worker_id": (job_state.worker_id if job_state else None),
        }
    )


# ==================== 辅助函数 ====================

_STAGE_ORDER = ["parsing", "vl_recognition", "chunking", "vectorizing", "completed"]
_STAGE_LABELS = {
    "parsing": "PDF解析",
    "vl_recognition": "VL图片识别",
    "chunking": "文本分块",
    "vectorizing": "向量化存储",
    "completed": "处理完成",
    "failed": "处理失败",
}


def _get_stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)


def _build_stages(current_stage: str, progress_str: str) -> list[dict]:
    """构建分阶段进度列表"""
    # queued/recovering/启动 等前置态视为 parsing 阶段的前序，让第一步显示 processing
    effective_stage = current_stage
    if effective_stage not in _STAGE_ORDER and effective_stage not in ("completed", "failed"):
        effective_stage = "parsing"
    stages = []
    current_idx = _STAGE_ORDER.index(effective_stage) if effective_stage in _STAGE_ORDER else -1

    for i, key in enumerate(_STAGE_ORDER):
        if key == "completed":
            continue  # 不在列表中展示"完成"阶段

        if effective_stage == "completed" or effective_stage == "failed":
            stage_status = "completed" if effective_stage == "completed" else "failed"
        elif i < current_idx:
            stage_status = "completed"
        elif i == current_idx:
            stage_status = "processing"
        else:
            stage_status = "pending"

        stage_data: dict = {
            "key": key,
            "label": _STAGE_LABELS.get(key, key),
            "status": stage_status,
        }

        # 当前阶段附加进度
        if i == current_idx and "/" in progress_str:
            try:
                parts = progress_str.split("/")
                stage_data["progress"] = int(parts[0])
                stage_data["total"] = int(parts[1])
            except (ValueError, IndexError):
                pass
        # 当前阶段为 processing 且 progress_str 不含 "/"
        # (即纯文案心跳, 如 "目录解析完成" / "页面降噪完成") 也透传 detail,
        # 让前端 mapper 可渲染子步骤文案, 避免 "处理中..." 长时间不变被误判卡死。
        elif i == current_idx and stage_status == "processing" and progress_str:
            stage_data["detail"] = progress_str

        stages.append(stage_data)

    return stages


def _compute_process_overall_progress(stages: list[dict]) -> int:
    if not stages:
        return 0
    completed_count = sum(1 for stage in stages if stage.get("status") == "completed")
    processing_stage = next(
        (stage for stage in stages if stage.get("status") == "processing"),
        None,
    )
    processing_ratio = 0.0
    if processing_stage:
        total = processing_stage.get("total") or 0
        progress = processing_stage.get("progress") or 0
        if isinstance(total, int) and total > 0 and isinstance(progress, int):
            processing_ratio = min(progress / total, 1.0)
    overall = (completed_count + processing_ratio) / len(stages)
    return int(overall * 100)


@router.get("/{document_id}/preview")
async def preview_document(
    project_id: int,
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """预览/下载原始 PDF 文档

    前端 PDF 查看器通过此接口获取文件流。
    需要项目所有者或管理员权限。
    """
    await _get_project_with_auth(project_id, current_user, db)

    service = DocumentService(db)
    document = await service.get_document_by_id_and_project(document_id, project_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )
    if not os.path.exists(document.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文件不存在于服务器",
        )
    if Path(document.file_path).suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="文档格式转换尚未完成",
        )

    return FileResponse(document.file_path, media_type="application/pdf")
