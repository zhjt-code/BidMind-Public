"""标书编写目录扩展路由。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, handle_service_error, project_log_context
from app.core.exceptions import ServiceError
from app.core.database import get_db
from app.models.user import User
from app.schemas.writing import OutlineNodeCreate, OutlineNodeResponse
from app.services.writing_service import WritingService

router = APIRouter(
    prefix="/writing/projects/{project_id}/outline",
    tags=["标书编写"],
    dependencies=[Depends(project_log_context)],
)


@router.post("", response_model=OutlineNodeResponse, status_code=status.HTTP_201_CREATED)
async def create_outline_node(
    project_id: int,
    body: OutlineNodeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """手动新增目录节点。"""
    try:
        node = await WritingService(db)._outline.create_outline_node(
            project_id,
            current_user,
            body.model_dump(exclude_unset=True),
        )
        return OutlineNodeResponse.model_validate(node)
    except ServiceError as e:
        handle_service_error(e)
