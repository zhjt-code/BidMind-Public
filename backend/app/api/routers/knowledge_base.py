"""知识库 API 路由。"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, handle_service_error
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import ServiceError
from app.core.rate_limit import limiter
from app.models.kb_document import (
    KnowledgeAuthority,
    KnowledgeFlow,
    KnowledgeOwnerScope,
    KnowledgeReviewStatus,
)
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.knowledge_base import (
    KnowledgeAtomProjectionRequest,
    KnowledgeAtomResponse,
    KnowledgeDocumentBase,
    KnowledgeDocumentResponse,
    KnowledgeDocumentUpdate,
    KnowledgeProjectionRequest,
    KnowledgeProjectionResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    ProjectKnowledgeUsageResponse,
    ProjectKnowledgeUsageUpdate,
)
from app.services.knowledge_base_service import KnowledgeService


router = APIRouter(prefix="/knowledge-base", tags=["知识库"])


def _svc(db: AsyncSession) -> KnowledgeService:
    return KnowledgeService(db)


@router.post(
    "/documents",
    response_model=ApiResponse[KnowledgeDocumentResponse],
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(lambda: settings.RATE_LIMIT_UPLOAD)
async def upload_knowledge_document(
    request: Request,
    title: str = Form(...),
    business_type: str = Form(...),
    applicable_flows: str = Form(default=""),
    business_label: Optional[str] = Form(default=None),
    valid_from: Optional[date] = Form(default=None),
    valid_to: Optional[date] = Form(default=None),
    source: Optional[str] = Form(default=None),
    authority: KnowledgeAuthority = Form(default=KnowledgeAuthority.user_experience),
    enabled: bool = Form(default=True),
    version: str = Form(default="1"),
    owner_scope: KnowledgeOwnerScope = Form(default=KnowledgeOwnerScope.personal),
    industry_tags: str = Form(default=""),
    region_tags: str = Form(default=""),
    review_status: KnowledgeReviewStatus = Form(default=KnowledgeReviewStatus.approved),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[KnowledgeDocumentResponse]:
    """上传知识库资料。"""
    try:
        content = await file.read()
        metadata = KnowledgeDocumentBase(
            title=title,
            applicable_flows=applicable_flows,
            business_type=business_type,
            business_label=business_label,
            valid_from=valid_from,
            valid_to=valid_to,
            source=source,
            authority=authority,
            enabled=enabled,
            version=version,
            owner_scope=owner_scope,
            industry_tags=industry_tags,
            region_tags=region_tags,
            review_status=review_status,
        )
        doc = await _svc(db).create_document(
            current_user=current_user,
            filename=file.filename or "knowledge_document",
            content=content,
            mime_type=file.content_type,
            metadata=metadata.model_dump(),
        )
        return ApiResponse(
            code=201,
            message="知识资料上传成功",
            data=KnowledgeDocumentResponse.model_validate(doc),
        )
    except ServiceError as e:
        handle_service_error(e)


@router.get("/documents", response_model=ApiResponse[list[KnowledgeDocumentResponse]])
async def list_knowledge_documents(
    applicable_flow: Optional[KnowledgeFlow] = None,
    business_type: Optional[str] = None,
    include_disabled: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[KnowledgeDocumentResponse]]:
    """列出可见知识资料。"""
    try:
        docs = await _svc(db).list_documents(
            current_user,
            applicable_flow=applicable_flow.value if applicable_flow else None,
            business_type=business_type,
            include_disabled=include_disabled,
        )
        return ApiResponse(
            data=[KnowledgeDocumentResponse.model_validate(doc) for doc in docs],
        )
    except ServiceError as e:
        handle_service_error(e)


@router.post(
    "/documents/{document_id}/process",
    response_model=ApiResponse[KnowledgeDocumentResponse],
)
async def process_knowledge_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[KnowledgeDocumentResponse]:
    """解析、分块并向量化知识资料。"""
    try:
        doc = await _svc(db).process_document(document_id, current_user)
        return ApiResponse(message="知识资料处理任务已启动", data=KnowledgeDocumentResponse.model_validate(doc))
    except ServiceError as e:
        handle_service_error(e)


@router.put("/documents/{document_id}", response_model=ApiResponse[KnowledgeDocumentResponse])
async def update_knowledge_document(
    document_id: int,
    payload: KnowledgeDocumentUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[KnowledgeDocumentResponse]:
    """更新知识资料元数据。"""
    try:
        doc = await _svc(db).update_document(
            document_id,
            current_user,
            payload.model_dump(exclude_none=True),
        )
        return ApiResponse(data=KnowledgeDocumentResponse.model_validate(doc))
    except ServiceError as e:
        handle_service_error(e)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_knowledge_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除知识资料。"""
    try:
        await _svc(db).delete_document(document_id, current_user)
    except ServiceError as e:
        handle_service_error(e)


@router.post("/search", response_model=ApiResponse[KnowledgeSearchResponse])
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[KnowledgeSearchResponse]:
    """检索知识库上下文。"""
    try:
        results = await _svc(db).search(
            query=payload.query,
            current_user=current_user,
            applicable_flow=payload.applicable_flow.value if payload.applicable_flow else None,
            business_types=payload.business_types,
            top_k=payload.top_k,
            min_similarity=payload.min_similarity,
        )
        return ApiResponse(data=KnowledgeSearchResponse(results=results))
    except ServiceError as e:
        handle_service_error(e)


@router.post(
    "/writing/projects/{project_id}/project",
    response_model=ApiResponse[KnowledgeProjectionResponse],
)
async def project_knowledge_to_writing(
    project_id: int,
    payload: KnowledgeProjectionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[KnowledgeProjectionResponse]:
    """将知识资料引用为当前写标项目素材。"""
    try:
        created, skipped, warnings = await _svc(db).project_to_writing_materials(
            project_id=project_id,
            current_user=current_user,
            kb_document_ids=payload.kb_document_ids,
        )
        return ApiResponse(
            message="知识资料引用完成",
            data=KnowledgeProjectionResponse(
                created_material_ids=created,
                skipped_document_ids=skipped,
                warnings=warnings,
            ),
        )
    except ServiceError as e:
        handle_service_error(e)


@router.get(
    "/documents/{document_id}/atoms",
    response_model=ApiResponse[list[KnowledgeAtomResponse]],
)
async def list_knowledge_atoms(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[KnowledgeAtomResponse]]:
    """列出知识资料的原子素材。"""
    try:
        atoms = await _svc(db).list_atoms(document_id, current_user)
        return ApiResponse(
            data=[KnowledgeAtomResponse.model_validate(atom) for atom in atoms],
        )
    except ServiceError as e:
        handle_service_error(e)


@router.post(
    "/writing/projects/{project_id}/project-atoms",
    response_model=ApiResponse[KnowledgeProjectionResponse],
)
async def project_knowledge_atoms_to_writing(
    project_id: int,
    payload: KnowledgeAtomProjectionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[KnowledgeProjectionResponse]:
    """将知识库原子素材引用为当前写标项目素材。"""
    try:
        created, skipped, warnings = await _svc(db).project_atoms_to_writing_materials(
            project_id=project_id,
            current_user=current_user,
            kb_atom_ids=payload.kb_atom_ids,
        )
        return ApiResponse(
            message="知识库原子素材引用完成",
            data=KnowledgeProjectionResponse(
                created_material_ids=created,
                skipped_document_ids=skipped,
                warnings=warnings,
            ),
        )
    except ServiceError as e:
        handle_service_error(e)


@router.get(
    "/projects/{project_id}/usage-config",
    response_model=ApiResponse[ProjectKnowledgeUsageResponse],
)
async def get_project_kb_usage_config(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProjectKnowledgeUsageResponse]:
    """读取项目级知识库使用配置。

    用于投标审查确认页和串标上传页回显当前授权状态；未配置时返回默认未启用。
    """
    try:
        usage = await _svc(db).get_project_usage_config(
            project_id=project_id,
            current_user=current_user,
        )
        return ApiResponse(data=ProjectKnowledgeUsageResponse(**usage.to_summary()))
    except ServiceError as e:
        handle_service_error(e)


@router.put(
    "/projects/{project_id}/usage-config",
    response_model=ApiResponse[ProjectKnowledgeUsageResponse],
)
async def update_project_kb_usage_config(
    project_id: int,
    payload: ProjectKnowledgeUsageUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProjectKnowledgeUsageResponse]:
    """更新项目级知识库使用配置。

    用户在投标审查确认页/串标上传页提交的"是否启用 + 选择资料"由该接口持久化。
    enabled=False 或未传 selected_kb_document_ids 时按"未启用"清空配置。
    """
    try:
        usage = await _svc(db).update_project_usage_config(
            project_id=project_id,
            current_user=current_user,
            enabled=payload.enabled,
            flow=payload.flow.value if payload.flow else None,
            selected_kb_document_ids=payload.selected_kb_document_ids,
            usage_policy=payload.usage_policy,
        )
        return ApiResponse(
            message="项目知识库使用配置已更新",
            data=ProjectKnowledgeUsageResponse(**usage.to_summary()),
        )
    except ServiceError as e:
        handle_service_error(e)
