"""标书编写图示编辑路由。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, handle_service_error, project_log_context
from app.core.database import get_db
from app.core.exceptions import ServiceError
from app.models.user import User
from app.schemas.writing_diagram import (
    WritingDiagramResponse,
    WritingDiagramUpdateRequest,
)
from app.services.writing.diagram_service import WritingSectionDiagramService
from app.services.writing_service import WritingService

router = APIRouter(
    prefix="/writing/projects/{project_id}/diagrams",
    tags=["标书编写"],
    dependencies=[Depends(project_log_context)],
)


def _diagram_service(db: AsyncSession) -> WritingSectionDiagramService:
    return WritingSectionDiagramService(
        db,
        render_invalidator=WritingService(db)._invalidate_render_artifact,
    )


async def _ensure_project_access(
    project_id: int,
    user: User,
    db: AsyncSession,
) -> None:
    await WritingService(db)._get_project_or_raise(project_id, user)


@router.get("/{diagram_id}", response_model=WritingDiagramResponse)
async def get_writing_diagram(
    project_id: int,
    diagram_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取章节图示编辑定义。"""

    try:
        await _ensure_project_access(project_id, current_user, db)
        return await _diagram_service(db).get_diagram_for_editing(
            project_id=project_id,
            diagram_id=diagram_id,
        )
    except ServiceError as exc:
        handle_service_error(exc)


@router.get("/{diagram_id}/png")
async def get_writing_diagram_png(
    project_id: int,
    diagram_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取章节图示 PNG 文件。"""

    try:
        await _ensure_project_access(project_id, current_user, db)
        png_path = await _diagram_service(db).resolve_diagram_png_path(
            project_id=project_id,
            diagram_id=diagram_id,
        )
        path = Path(png_path)
        return FileResponse(
            path=str(path),
            media_type="image/png",
            filename=path.name,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except ServiceError as exc:
        handle_service_error(exc)


@router.put("/{diagram_id}", response_model=WritingDiagramResponse)
async def update_writing_diagram(
    project_id: int,
    diagram_id: int,
    body: WritingDiagramUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """人工编辑章节图示并重绘。"""

    try:
        await _ensure_project_access(project_id, current_user, db)
        return await _diagram_service(db).apply_diagram_edit(
            project_id=project_id,
            diagram_id=diagram_id,
            body=body,
        )
    except ServiceError as exc:
        handle_service_error(exc)
