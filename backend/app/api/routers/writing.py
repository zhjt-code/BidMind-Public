"""标书编写 API 路由

覆盖标书编写全流程:
  约束提取 -> 目录规划 -> 素材管理 -> 匹配 -> 内容生成 -> 输出
"""

from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, handle_service_error, project_log_context
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import ServiceError
from app.core.filename import build_export_filename
from app.core.rate_limit import limiter
from app.models.user import User
from app.schemas.writing import (
    ClauseCoverageResponse,
    ConsistencyIssue,
    MaterialBatchStatusResponse,
    OutlineNodeResponse,
    OutlineNodeUpdate,
    OutlineReorderRequest,
    SectionDraftResponse,
    SectionDraftUpdate,
    WritingBindingConfirmRequest,
    WritingBindingResponse,
    WritingBindingUpdate,
    WritingClauseConfirmRequest,
    WritingClauseCreate,
    WritingClauseResponse,
    WritingClauseUpdate,
    WritingMaterialResponse,
    WritingMaterialTitleBatchUpdateRequest,
    WritingMaterialTitleBatchUpdateResponse,
    WritingMaterialTitleUpdateRequest,
    WritingMaterialUpdateRequest,
    WritingProjectStatusResponse,
    WritingRetryRequest,
    WritingRetryStateResponse,
    WritingRetryTriggerResponse,
)
from app.services.retry_service import RetryService
from app.services.writing_service import WritingService

router = APIRouter(
    prefix="/writing/projects/{project_id}",
    tags=["标书编写"],
    dependencies=[Depends(project_log_context)],
)


def _svc(db: AsyncSession) -> WritingService:
    return WritingService(db)


# ==================== 项目状态 ====================

@router.get("/status", response_model=WritingProjectStatusResponse)
async def get_project_status(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取标书编写项目状态"""
    try:
        return await _svc(db).get_project_status(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.get("/retry-state", response_model=WritingRetryStateResponse)
async def get_retry_state(
    project_id: int,
    node_key: str = Query(default="writing_material_process"),
    target_id: Optional[int] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询显式重试状态。"""
    try:
        return await RetryService(db).get_retry_state(
            project_id,
            current_user,
            node_key=node_key,
            target_id=target_id,
        )
    except ServiceError as e:
        handle_service_error(e)


@router.post("/retry", response_model=WritingRetryTriggerResponse)
async def retry_writing_node(
    project_id: int,
    body: WritingRetryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """显式重试写标节点。"""
    try:
        return await RetryService(db).retry_writing_node(
            project_id,
            current_user,
            node_key=body.node_key,
            target_id=body.target_id,
        )
    except ServiceError as e:
        handle_service_error(e)


# ==================== 阶段1: 约束提取 ====================

@router.post("/extract")
async def trigger_extract(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """触发约束提取"""
    try:
        return await _svc(db).trigger_extract(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.get("/clauses", response_model=list[WritingClauseResponse])
async def list_clauses(
    project_id: int,
    layer: Optional[str] = Query(default=None, description="按层级筛选"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取约束列表"""
    try:
        clauses = await _svc(db).list_clauses(project_id, current_user, layer)
        return [WritingClauseResponse.model_validate(c) for c in clauses]
    except ServiceError as e:
        handle_service_error(e)


@router.get("/clauses/scoring-report")
async def get_scoring_report(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """评分项按卷满分核计报告（约束确认页横幅；无评分项返回 null）"""
    try:
        return await _svc(db).get_scoring_report(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.put("/clauses/{clause_id}", response_model=WritingClauseResponse)
async def update_clause(
    project_id: int,
    clause_id: int,
    body: WritingClauseUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """编辑约束"""
    try:
        clause = await _svc(db).update_clause(
            project_id, clause_id, current_user, body.model_dump(exclude_none=True)
        )
        return WritingClauseResponse.model_validate(clause)
    except ServiceError as e:
        handle_service_error(e)


@router.post("/clauses", response_model=WritingClauseResponse)
async def create_clause(
    project_id: int,
    body: WritingClauseCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """手动新增约束"""
    try:
        clause = await _svc(db).create_clause(
            project_id, current_user, body.model_dump(exclude_none=True)
        )
        return WritingClauseResponse.model_validate(clause)
    except ServiceError as e:
        handle_service_error(e)


@router.delete("/clauses/{clause_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_clause(
    project_id: int,
    clause_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除约束"""
    try:
        await _svc(db).delete_clause(project_id, clause_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.post("/clauses/confirm")
async def confirm_clauses(
    project_id: int,
    body: WritingClauseConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量确认约束"""
    try:
        count = await _svc(db).confirm_clauses(project_id, body.clause_ids, current_user)
        return {"confirmed": count}
    except ServiceError as e:
        handle_service_error(e)


# ==================== 阶段2: 目录规划 ====================

@router.post("/outline/generate")
async def generate_outline(
    project_id: int,
    force: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """触发目录树生成(后台任务)

    Args:
        force: 为 true 时清空已有目录并重建，默认 false 幂等跳过。
    """
    try:
        return await _svc(db).generate_outline(project_id, current_user, force=force)
    except ServiceError as e:
        handle_service_error(e)


@router.post("/outline/profiles/regenerate")
async def regenerate_chapter_profiles(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """幂等重跑章节画像（不重建目录树）。

 用于存量项目修复 覆盖率死区后的画像回填。
 """
    from app.services.writing.profile_backfill_service import ProfileBackfillService

    try:
        writing = _svc(db)
        return await ProfileBackfillService(
            db,
            project_resolver=lambda *a, **kw: writing._get_project_or_raise(*a, **kw),
        ).regenerate_chapter_profiles(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.get("/outline", response_model=list[OutlineNodeResponse])
async def get_outline(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取目录树"""
    try:
        nodes = await _svc(db).get_outline(project_id, current_user)
        return [OutlineNodeResponse.model_validate(n) for n in nodes]
    except ServiceError as e:
        handle_service_error(e)


@router.put("/outline/{node_id}", response_model=OutlineNodeResponse)
async def update_outline_node(
    project_id: int,
    node_id: int,
    body: OutlineNodeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """编辑目录节点"""
    try:
        node = await _svc(db).update_outline_node(
            project_id, node_id, current_user, body.model_dump(exclude_unset=True)
        )
        return OutlineNodeResponse.model_validate(node)
    except ServiceError as e:
        handle_service_error(e)


@router.delete("/outline/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_outline_node(
    project_id: int,
    node_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除目录节点"""
    try:
        await _svc(db).delete_outline_node(project_id, node_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.post("/outline/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_outline(
    project_id: int,
    body: OutlineReorderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """拖拽排序"""
    try:
        await _svc(db).reorder_outline(
            project_id, body.node_ids, body.parent_id, current_user
        )
    except ServiceError as e:
        handle_service_error(e)


# ==================== 素材管理 ====================

@router.post("/materials/upload")
@limiter.limit(lambda: settings.RATE_LIMIT_UPLOAD)
async def upload_material(
    request: Request,
    project_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """保存素材文件(阶段1: 仅保存到磁盘+创建DB记录,不做LLM处理)"""
    try:
        file_bytes = await file.read()
        mat = await _svc(db).save_material(
            project_id, current_user, file.filename or "unknown", file_bytes
        )
        return WritingMaterialResponse.model_validate(mat)
    except ServiceError as e:
        handle_service_error(e)


@router.post("/materials/process-batch")
async def process_materials_batch(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """统一批处理所有待处理素材(阶段2)

    将所有 saved 状态素材统一解析,图片汇入VL管线并发识别,
    文字走审查模型打标。
    """
    try:
        result = await _svc(db).process_materials_batch(
            project_id, current_user,
        )
        return result
    except ServiceError as e:
        handle_service_error(e)


@router.get("/materials/process-status", response_model=MaterialBatchStatusResponse)
async def get_materials_process_status(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询素材批处理进度"""
    try:
        return await _svc(db).get_batch_process_status(
            project_id, current_user,
        )
    except ServiceError as e:
        handle_service_error(e)


@router.get("/materials", response_model=list[WritingMaterialResponse])
async def list_materials(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """素材列表"""
    try:
        materials = await _svc(db).list_materials(project_id, current_user)
        return [WritingMaterialResponse.model_validate(m) for m in materials]
    except ServiceError as e:
        handle_service_error(e)


@router.put("/materials/{material_id}", response_model=WritingMaterialResponse)
async def update_material(
    project_id: int,
    material_id: int,
    body: WritingMaterialUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新素材拆分状态。"""
    try:
        material = await _svc(db).update_material_split_state(
            project_id, material_id, body.action, current_user
        )
        return WritingMaterialResponse.model_validate(material)
    except ServiceError as e:
        handle_service_error(e)


@router.patch("/materials/{material_id}/title", response_model=WritingMaterialResponse)
async def update_material_title(
    project_id: int,
    material_id: int,
    body: WritingMaterialTitleUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新素材展示标题。"""
    try:
        material = await _svc(db).update_material_title(
            project_id,
            material_id,
            body.display_title,
            current_user,
        )
        return WritingMaterialResponse.model_validate(material)
    except ServiceError as e:
        handle_service_error(e)


@router.post(
    "/materials/titles/batch",
    response_model=WritingMaterialTitleBatchUpdateResponse,
)
async def batch_update_material_titles(
    project_id: int,
    body: WritingMaterialTitleBatchUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量更新素材展示标题。"""
    try:
        return await _svc(db).batch_update_material_titles(
            project_id,
            [
                {
                    "material_id": item.material_id,
                    "display_title": item.display_title,
                }
                for item in body.items
            ],
            current_user,
        )
    except ServiceError as e:
        handle_service_error(e)


@router.get("/materials/{material_id}/preview")
async def get_material_preview(
    project_id: int,
    material_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取素材原文 PDF 预览。"""
    try:
        preview_path = await _svc(db).get_material_preview_path(
            project_id,
            material_id,
            current_user,
        )
    except ServiceError as e:
        handle_service_error(e)
    return FileResponse(
        path=str(preview_path),
        media_type="application/pdf",
        filename=Path(preview_path).name,
    )


@router.get("/materials/checklist")
async def get_material_checklist(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """素材缺失清单"""
    try:
        return await _svc(db).get_material_checklist(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.delete("/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(
    project_id: int,
    material_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除素材"""
    try:
        await _svc(db).delete_material(project_id, material_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


# ==================== 素材图片服务 ====================


@router.get("/nodes/{node_id}/images")
async def list_node_images(
    project_id: int,
    node_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取章节节点绑定的素材关联的所有图片列表

    通过 WritingBinding(material_to_node) 找到绑定素材,
    再从 WritingMaterialImage 查询每个素材的图片记录。
    """
    from sqlalchemy import select as _sel

    from app.models.writing_binding import BindingType, WritingBinding
    from app.models.writing_material_image import WritingMaterialImage

    await _svc(db)._get_project_or_raise(project_id, current_user)

    mat_result = await db.execute(
        _sel(WritingBinding.material_id).where(
            WritingBinding.project_id == project_id,
            WritingBinding.outline_node_id == node_id,
            WritingBinding.material_id.isnot(None),
            WritingBinding.binding_type == BindingType.material_to_node,
        )
    )
    material_ids = [r[0] for r in mat_result.all()]

    if not material_ids:
        return []

    img_result = await db.execute(
        _sel(WritingMaterialImage).where(
            WritingMaterialImage.material_id.in_(material_ids),
        ).order_by(WritingMaterialImage.material_id, WritingMaterialImage.image_index)
    )
    records = list(img_result.scalars().all())

    return [
        {
            "id": r.id,
            "caption": r.caption,
            "imageType": r.image_type.value if r.image_type else None,
            "width": r.width,
            "height": r.height,
        }
        for r in records
    ]


@router.get("/images/{image_id}")
async def get_material_image(
    project_id: int,
    image_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取素材中的图片文件

    通过 WritingMaterialImage.id 返回对应的图片文件。
    验证图片所属素材归属于当前项目。
    """
    from pathlib import Path

    from sqlalchemy import select as _sel

    from app.models.writing_material import WritingMaterial
    from app.models.writing_material_image import WritingMaterialImage

    await _svc(db)._get_project_or_raise(project_id, current_user)

    result = await db.execute(
        _sel(WritingMaterialImage).join(
            WritingMaterial,
            WritingMaterialImage.material_id == WritingMaterial.id,
        ).where(
            WritingMaterialImage.id == image_id,
            WritingMaterial.project_id == project_id,
        )
    )
    img_record = result.scalars().first()

    if not img_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"图片不存在: id={image_id}",
        )

    img_path = Path(img_record.image_path)
    if not img_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="图片文件不存在",
        )

    ext = img_path.suffix.lower().lstrip(".")
    media_types = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "gif": "image/gif",
        "bmp": "image/bmp", "webp": "image/webp",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(img_path),
        media_type=media_type,
        filename=img_path.name,
    )


# ==================== 阶段3: 匹配 ====================

@router.post("/match")
async def trigger_match(
    project_id: int,
    reset_reviewed_bindings: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """触发三元图匹配(后台任务)

 reset_reviewed_bindings=False（默认）保留人工 confirmed/rejected，仅重算
 suggested；显式传 true 时清空全部绑定后重新匹配。
 """
    try:
        return await _svc(db).trigger_match(
            project_id, current_user,
            reset_reviewed_bindings=reset_reviewed_bindings,
        )
    except ServiceError as e:
        handle_service_error(e)


@router.get("/bindings", response_model=list[WritingBindingResponse])
async def list_bindings(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """绑定列表"""
    try:
        bindings = await _svc(db).list_bindings(project_id, current_user)
        return [WritingBindingResponse.model_validate(b) for b in bindings]
    except ServiceError as e:
        handle_service_error(e)


@router.put("/bindings/{binding_id}", response_model=WritingBindingResponse)
async def update_binding(
    project_id: int,
    binding_id: int,
    body: WritingBindingUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """调整绑定"""
    try:
        binding = await _svc(db).update_binding(
            project_id, binding_id, current_user, body.model_dump(exclude_none=True)
        )
        return WritingBindingResponse.model_validate(binding)
    except ServiceError as e:
        handle_service_error(e)


@router.post("/bindings/confirm")
async def confirm_bindings(
    project_id: int,
    body: WritingBindingConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量确认绑定"""
    try:
        count = await _svc(db).confirm_bindings(project_id, body.binding_ids, current_user)
        return {"confirmed": count}
    except ServiceError as e:
        handle_service_error(e)


# ==================== 阶段4: 内容生成 ====================

@router.post("/compose")
async def trigger_compose(
    project_id: int,
    force: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """触发全文生成"""
    try:
        return await _svc(db).trigger_compose(project_id, current_user, force=force)
    except ServiceError as e:
        handle_service_error(e)


@router.get("/compose/status")
async def get_compose_status(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询全文生成任务进度"""
    from app.core.config import settings
    from app.core.tasks import inspect_task_liveness
    from app.core.tasks.writing_compose_task import get_writing_compose_task_name

    task_name = get_writing_compose_task_name(project_id)
    liveness = inspect_task_liveness(
        task_name,
        heartbeat_timeout=int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180)),
        arq_function="writing_compose_job",
    )
    heartbeat = liveness.heartbeat or {}

    progress_raw = heartbeat.get("progress", "")
    progress_pct = 0
    current_detail = ""
    if progress_raw and "%" in progress_raw:
        try:
            progress_pct = int(progress_raw.split("%")[0])
        except (ValueError, IndexError):
            pass
    if progress_raw and "|" in progress_raw:
        current_detail = progress_raw.split("|", 1)[1]

    return {
        "active": liveness.is_live,
        "stage": heartbeat.get("stage") if heartbeat else ("failed" if liveness.state in {"stale", "missing"} else None),
        "progress": progress_raw,
        "progress_pct": min(progress_pct, 100),
        "current_detail": current_detail or (
            "生成任务异常终止，请重试" if liveness.state in {"stale", "missing"} else ""
        ),
    }


@router.post("/compose/{node_id}")
async def compose_single(
    project_id: int,
    node_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """触发单章节生成(后台任务).

    返回 ``{"task_name", "queue_mode", "message"}``; 前端通过
    ``GET /compose/{node_id}/status`` 轮询进度, 完成后重新拉取 ``/drafts/{node_id}``。
    """
    try:
        return await _svc(db).compose_single_node(project_id, node_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.get("/compose/{node_id}/status")
async def get_compose_single_status(
    project_id: int,
    node_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询单章节生成任务进度."""
    try:
        return await _svc(db).get_compose_single_status(project_id, node_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.get("/drafts", response_model=list[SectionDraftResponse])
async def list_drafts(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """全部草稿"""
    try:
        drafts = await _svc(db).list_drafts(project_id, current_user)
        return [SectionDraftResponse.model_validate(d) for d in drafts]
    except ServiceError as e:
        handle_service_error(e)


@router.get("/drafts/{node_id}", response_model=SectionDraftResponse)
async def get_draft(
    project_id: int,
    node_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """单章节草稿"""
    try:
        draft = await _svc(db).get_draft(project_id, node_id, current_user)
        return SectionDraftResponse.model_validate(draft)
    except ServiceError as e:
        handle_service_error(e)


@router.put("/drafts/{node_id}", response_model=SectionDraftResponse)
async def update_draft(
    project_id: int,
    node_id: int,
    body: SectionDraftUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户编辑保存"""
    try:
        draft = await _svc(db).update_draft(project_id, node_id, current_user, body.content)
        return SectionDraftResponse.model_validate(draft)
    except ServiceError as e:
        handle_service_error(e)


@router.post("/drafts/{node_id}/lock", response_model=SectionDraftResponse)
async def toggle_lock(
    project_id: int,
    node_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """锁定/解锁"""
    try:
        draft = await _svc(db).toggle_lock(project_id, node_id, current_user)
        return SectionDraftResponse.model_validate(draft)
    except ServiceError as e:
        handle_service_error(e)


@router.get("/consistency", response_model=list[ConsistencyIssue])
async def get_consistency(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """一致性检查结果"""
    try:
        issues = await _svc(db).get_consistency_issues(project_id, current_user)
        return [ConsistencyIssue(**i) for i in issues]
    except ServiceError as e:
        handle_service_error(e)


@router.get("/clause-coverage", response_model=ClauseCoverageResponse)
async def get_clause_coverage(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """约束消费覆盖诊断（全局分母/已覆盖 + 未覆盖三类可行动原因）"""
    try:
        data = await _svc(db).get_clause_coverage(project_id, current_user)
        return ClauseCoverageResponse.model_validate(data)
    except ServiceError as e:
        handle_service_error(e)


# ==================== 阶段5: 输出 ====================

@router.post("/render")
async def trigger_render(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """触发渲染"""
    try:
        retry_svc = RetryService(db)
        task_name = f"writing_render_{project_id}"
        await retry_svc.begin_attempt(
            project_id=project_id,
            node_key=RetryService.NODE_WRITING_RENDER,
            user_id=getattr(current_user, "id", None),
            task_name=task_name,
            explicit_retry=False,
            last_stage="rendering",
        )
        await retry_svc.mark_task_running(
            project_id,
            node_key=RetryService.NODE_WRITING_RENDER,
            stage="rendering",
        )
        await _svc(db).render_docx(project_id, current_user)
        await retry_svc.mark_task_completed(
            project_id,
            node_key=RetryService.NODE_WRITING_RENDER,
        )
        return {"message": "渲染完成"}
    except Exception as e:
        try:
            await RetryService(db).mark_task_failed(
                project_id,
                node_key=RetryService.NODE_WRITING_RENDER,
                stage="failed",
                error=str(e),
            )
        except Exception:
            pass
        if isinstance(e, ServiceError):
            handle_service_error(e)
        raise


@router.get("/preview")
async def get_preview(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """HTML预览"""
    try:
        html = await _svc(db).get_html_preview(project_id, current_user)
        return Response(content=html, media_type="text/html; charset=utf-8")
    except ServiceError as e:
        handle_service_error(e)


@router.get("/download/docx")
async def download_docx(
    project_id: int,
    allow_blocking_issues: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载 Word 文档"""
    try:
        service = _svc(db)
        project = await service._get_project_or_raise(project_id, current_user)
        docx_bytes = await service.get_rendered_docx(
            project_id,
            current_user,
            allow_blocking_issues=allow_blocking_issues,
        )
        filename = build_export_filename(project.name, "标书初稿", "docx")
        encoded_filename = quote(filename)
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            },
        )
    except ServiceError as e:
        handle_service_error(e)
