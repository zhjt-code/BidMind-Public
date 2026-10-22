"""文档质检路由

提供投标审查项目的文档质检结果读取与发现项人工处置 API。
分层约束: 只做请求校验/响应格式化/异常转换, 业务在 read_service。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_user,
    handle_service_error,
    project_log_context,
)
from app.core.database import get_db
from app.core.exceptions import ServiceError
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.document_qa import FindingDispositionRequest
from app.services.document_qa.read_service import (
    load_document_quality_view,
    update_finding_disposition,
)

router = APIRouter(
    prefix="/projects/{project_id}/document-quality",
    dependencies=[Depends(project_log_context)],
)


@router.get("", response_model=ApiResponse)
async def get_document_quality(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """获取文档质检结果(scan 覆盖信息 + 汇总 + 发现项列表)"""
    try:
        data = await load_document_quality_view(db, project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)
    return ApiResponse(code=200, message="success", data=data)


@router.patch("/findings/{finding_id}/disposition", response_model=ApiResponse)
async def patch_finding_disposition(
    project_id: int,
    finding_id: int,
    payload: FindingDispositionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """人工处置发现项(确认问题/误报/忽略; 乐观锁版本冲突返回 409)"""
    try:
        data = await update_finding_disposition(
            db, project_id, finding_id, current_user,
            disposition=payload.disposition,
            note=payload.note,
            expected_version=payload.expected_version,
        )
    except ServiceError as e:
        handle_service_error(e)
    return ApiResponse(code=200, message="处置已保存", data=data)
