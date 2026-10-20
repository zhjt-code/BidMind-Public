"""检索 Schema 定义

定义语义检索相关的请求和响应数据模型。
"""

from typing import Optional
from pydantic import BaseModel, Field

from app.models.document import DocumentType


class SearchRequest(BaseModel):
    """检索请求"""
    query: str = Field(..., min_length=1, max_length=2000, description="查询文本")
    top_k: int = Field(default=5, ge=1, le=20, description="返回结果数量")
    min_similarity: float = Field(default=0.0, ge=0.0, le=1.0, description="最小相似度阈值")
    doc_type: Optional[str] = Field(default=None, description="文档类型筛选: tender/bid")


class SearchResultItem(BaseModel):
    """单个检索结果"""
    chunk_id: int = Field(..., description="分块ID")
    document_id: int = Field(..., description="文档ID")
    content: str = Field(..., description="分块内容")
    page_num: Optional[int] = Field(default=None, description="来源页码")
    similarity: float = Field(..., description="相似度分数")
    doc_type: Optional[str] = Field(default=None, description="文档类型")


class SearchResponse(BaseModel):
    """检索响应"""
    query: str = Field(..., description="查询文本")
    results: list[SearchResultItem] = Field(..., description="检索结果列表")
    total: int = Field(..., description="结果数量")


class RelatedContentRequest(BaseModel):
    """查找相关投标内容请求"""
    requirement: str = Field(
        ..., 
        min_length=1, 
        max_length=5000, 
        description="招标要求内容"
    )
    top_k: int = Field(default=5, ge=1, le=20, description="返回结果数量")
