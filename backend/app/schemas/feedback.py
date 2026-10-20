"""反馈相关的 Pydantic Schema

定义反馈提交、响应、列表和状态更新的数据结构。
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    """用户提交反馈的请求体"""
    feedback_type: str = Field(
        ...,
        pattern="^(bug|feature|review_question|other)$",
        description="反馈类型"
    )
    content: str = Field(
        ..., min_length=5, max_length=2000,
        description="反馈内容"
    )
    page_url: Optional[str] = Field(
        None, max_length=500,
        description="来源页面URL"
    )
    project_id: Optional[int] = Field(
        None, description="关联项目ID"
    )
    context_info: Optional[dict] = Field(
        None, description="浏览器/环境信息"
    )


class FeedbackUserInfo(BaseModel):
    """反馈中的用户简要信息"""
    id: int
    username: str
    email: str


class FeedbackProjectInfo(BaseModel):
    """反馈中的项目简要信息"""
    id: int
    name: str


class FeedbackResponse(BaseModel):
    """单条反馈的响应体"""
    id: int
    user_id: int
    user: Optional[FeedbackUserInfo] = None
    feedback_type: str
    content: str
    page_url: Optional[str] = None
    project_id: Optional[int] = None
    project: Optional[FeedbackProjectInfo] = None
    context_info: Optional[dict] = None
    status: str
    admin_note: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FeedbackListResponse(BaseModel):
    """反馈列表响应"""
    items: List[FeedbackResponse]
    total: int
    page: int
    page_size: int


class FeedbackStatusUpdate(BaseModel):
    """管理员更新反馈状态的请求体"""
    status: str = Field(
        ...,
        pattern="^(pending|processing|resolved|closed)$",
        description="反馈状态"
    )
    admin_note: Optional[str] = Field(
        None, max_length=1000,
        description="管理员批注"
    )
    notify_user: bool = Field(
        True,
        description="是否通过站内信通知用户（默认通知）"
    )
