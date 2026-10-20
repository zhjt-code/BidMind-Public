"""语义检索路由

提供语义检索相关的 API 端点。
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.database import get_db
from app.models.document import DocumentType
from app.core.retriever import SemanticRetriever, SearchResult
from app.schemas.search import (
    SearchRequest,
    SearchResultItem,
    SearchResponse,
    RelatedContentRequest,
)
from app.schemas.common import ApiResponse
from app.models.user import User
from app.models.project import Project
from app.api.dependencies import get_current_user, check_project_permission, project_log_context


router = APIRouter(
    prefix="/projects/{project_id}/search",
    dependencies=[Depends(project_log_context)],
)


async def _get_project_with_auth(
    project_id: int, db: AsyncSession, current_user: User
) -> Project:
    """查询项目并校验当前用户权限"""
    from sqlalchemy import select as sa_select

    result = await db.execute(sa_select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    check_project_permission(project, current_user)
    return project


def _to_search_result_item(result: SearchResult) -> SearchResultItem:
    """转换检索结果为响应格式"""
    return SearchResultItem(
        chunk_id=result.chunk_id,
        document_id=result.document_id,
        content=result.content,
        page_num=result.page_num,
        similarity=round(result.similarity, 4),
        doc_type=result.doc_type.value if result.doc_type else None
    )


@router.post("", response_model=ApiResponse[SearchResponse])
async def search_project(
    project_id: int,
    request: SearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[SearchResponse]:
    """在项目中语义检索
    
    支持在项目的招标文件、投标文件或全部文件中检索。
    
    Args:
        project_id: 项目ID
        request: 检索请求
        db: 数据库会话
        
    Returns:
        ApiResponse[SearchResponse]: 检索结果
    """
    await _get_project_with_auth(project_id, db, current_user)

    doc_type = None
    if request.doc_type:
        try:
            doc_type = DocumentType(request.doc_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无效的文档类型: {request.doc_type}"
            )
    
    retriever = SemanticRetriever(db)
    
    results = await retriever.search_in_project(
        project_id=project_id,
        query=request.query,
        top_k=request.top_k,
        min_similarity=request.min_similarity,
        doc_type=doc_type
    )
    
    logger.debug(f"检索完成: project={project_id}, query='{request.query[:30]}...', found={len(results)}")
    
    return ApiResponse(
        code=200,
        message="success",
        data=SearchResponse(
            query=request.query,
            results=[_to_search_result_item(r) for r in results],
            total=len(results)
        )
    )


@router.get("/tender", response_model=ApiResponse[SearchResponse])
async def search_tender(
    project_id: int,
    query: str = Query(..., min_length=1, description="查询文本"),
    top_k: int = Query(default=5, ge=1, le=20, description="返回结果数量"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[SearchResponse]:
    """在招标文件中检索
    
    Args:
        project_id: 项目ID
        query: 查询文本
        top_k: 返回结果数量
        db: 数据库会话
        
    Returns:
        ApiResponse[SearchResponse]: 检索结果
    """
    await _get_project_with_auth(project_id, db, current_user)

    retriever = SemanticRetriever(db)
    
    results = await retriever.search_tender(
        project_id=project_id,
        query=query,
        top_k=top_k
    )
    
    return ApiResponse(
        code=200,
        message="success",
        data=SearchResponse(
            query=query,
            results=[_to_search_result_item(r) for r in results],
            total=len(results)
        )
    )


@router.get("/bid", response_model=ApiResponse[SearchResponse])
async def search_bid(
    project_id: int,
    query: str = Query(..., min_length=1, description="查询文本"),
    top_k: int = Query(default=5, ge=1, le=20, description="返回结果数量"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[SearchResponse]:
    """在投标文件中检索
    
    Args:
        project_id: 项目ID
        query: 查询文本
        top_k: 返回结果数量
        db: 数据库会话
        
    Returns:
        ApiResponse[SearchResponse]: 检索结果
    """
    await _get_project_with_auth(project_id, db, current_user)

    retriever = SemanticRetriever(db)
    
    results = await retriever.search_bid(
        project_id=project_id,
        query=query,
        top_k=top_k
    )
    
    return ApiResponse(
        code=200,
        message="success",
        data=SearchResponse(
            query=query,
            results=[_to_search_result_item(r) for r in results],
            total=len(results)
        )
    )


@router.post("/related-bid", response_model=ApiResponse[SearchResponse])
async def find_related_bid_content(
    project_id: int,
    request: RelatedContentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[SearchResponse]:
    """根据招标要求查找相关投标内容
    
    核心业务接口：给定一条招标要求，在投标文件中找到
    最相关的响应内容，用于后续的对比审查。
    
    Args:
        project_id: 项目ID
        request: 请求体（包含招标要求内容）
        db: 数据库会话
        
    Returns:
        ApiResponse[SearchResponse]: 相关的投标内容
    """
    await _get_project_with_auth(project_id, db, current_user)

    retriever = SemanticRetriever(db)
    
    results = await retriever.find_related_bid_content(
        project_id=project_id,
        tender_requirement=request.requirement,
        top_k=request.top_k
    )
    
    logger.info(
        f"查找投标响应: project={project_id}, "
        f"requirement='{request.requirement[:30]}...', found={len(results)}"
    )
    
    return ApiResponse(
        code=200,
        message="success",
        data=SearchResponse(
            query=request.requirement,
            results=[_to_search_result_item(r) for r in results],
            total=len(results)
        )
    )
