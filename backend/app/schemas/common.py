"""通用 Schema 定义

定义 API 响应格式、分页参数等通用数据结构。
"""

from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应格式
    
    所有 API 响应都遵循此格式：
    - code: HTTP 状态码
    - message: 状态消息
    - data: 响应数据（可选）
    """
    code: int = Field(default=200, description="状态码")
    message: str = Field(default="success", description="状态消息")
    data: Optional[T] = Field(default=None, description="响应数据")


class ErrorResponse(BaseModel):
    """错误响应格式"""
    code: int = Field(..., description="错误码")
    message: str = Field(..., description="错误消息")
    detail: Optional[str] = Field(default=None, description="详细错误信息")


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(default=1, ge=1, description="页码（从1开始）")
    page_size: int = Field(default=20, ge=1, le=500, description="每页数量")
    
    @property
    def offset(self) -> int:
        """计算偏移量"""
        return (self.page - 1) * self.page_size
    
    @property
    def limit(self) -> int:
        """获取限制数量"""
        return self.page_size


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应格式"""
    items: list[T] = Field(..., description="数据列表")
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    total_pages: int = Field(..., description="总页数")
    
    @classmethod
    def create(
        cls,
        items: list[Any],
        total: int,
        page: int,
        page_size: int
    ) -> "PaginatedResponse":
        """创建分页响应
        
        Args:
            items: 数据列表
            total: 总数量
            page: 当前页码
            page_size: 每页数量
            
        Returns:
            PaginatedResponse: 分页响应对象
        """
        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )
