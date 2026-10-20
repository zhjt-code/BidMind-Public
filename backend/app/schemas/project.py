"""项目 Schema 定义

定义项目相关的请求和响应数据模型。
"""

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field

from app.models.project import ProjectStatus, ProjectType


class ProjectBase(BaseModel):
    """项目基础字段"""
    name: str = Field(..., min_length=1, max_length=200, description="项目名称")
    description: Optional[str] = Field(
        default=None, 
        max_length=1000, 
        description="项目描述"
    )


class ProjectCreate(ProjectBase):
    """创建项目请求

    示例（投标审查）:
        {"name": "某采购项目审查", "project_type": "bid_review"}
    示例（串标审查）:
        {"name": "某采购串标分析", "project_type": "collusion_check"}
    """
    project_type: Optional[ProjectType] = Field(
        default=ProjectType.BID_REVIEW,
        description="项目类型：bid_review=投标审查，collusion_check=串标审查"
    )


class ProjectUpdate(BaseModel):
    """更新项目请求
    
    所有字段可选，只更新提供的字段。
    """
    name: Optional[str] = Field(
        default=None, 
        min_length=1, 
        max_length=200, 
        description="项目名称"
    )
    description: Optional[str] = Field(
        default=None, 
        max_length=1000, 
        description="项目描述"
    )


class OwnerInfo(BaseModel):
    """项目所有者简要信息"""
    id: int
    username: str
    
    class Config:
        from_attributes = True


class ProjectResponse(BaseModel):
    """项目响应
    
    返回项目详细信息。
    """
    id: int = Field(..., description="项目ID")
    name: str = Field(..., description="项目名称")
    description: Optional[str] = Field(default=None, description="项目描述")
    status: ProjectStatus = Field(..., description="项目状态")
    project_type: ProjectType = Field(default=ProjectType.BID_REVIEW, description="项目类型")
    owner_id: Optional[int] = Field(default=None, description="所有者ID")
    owner: Optional[OwnerInfo] = Field(default=None, description="所有者信息")
    error_message: Optional[str] = Field(
        default=None,
        description="失败时的错误信息（仅 status=failed 时有业务语义）",
    )
    run_mode: str = Field(
        default="guided",
        description="运行模式: guided=人工确认 / express=一步到位",
    )
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    
    class Config:
        from_attributes = True  # 支持从 ORM 模型转换


class ProjectListResponse(BaseModel):
    """项目列表响应"""
    items: list[ProjectResponse] = Field(..., description="项目列表")
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    total_pages: int = Field(..., description="总页数")


class BatchDeleteRequest(BaseModel):
    """批量删除项目请求"""
    project_ids: list[int] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="待删除的项目ID列表，最多50个",
    )


class ProjectStatusResponse(BaseModel):
    """项目状态响应"""
    id: int = Field(..., description="项目ID")
    status: ProjectStatus = Field(..., description="项目状态")
    message: Optional[str] = Field(default=None, description="状态消息")


class ProjectRunModeUpdate(BaseModel):
    """单独更新运行模式，不走通用 ProjectUpdate。"""

    run_mode: Literal["guided", "express"] = Field(..., description="guided=人工确认 / express=一步到位")
