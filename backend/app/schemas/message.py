"""站内信相关的 Pydantic Schema

定义消息响应、列表和未读数的数据结构。
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    """单条消息的响应体"""
    id: int
    user_id: int
    type: str
    title: str
    content: str
    is_read: bool
    read_at: Optional[datetime] = None
    ref_type: Optional[str] = None
    ref_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageListResponse(BaseModel):
    """消息列表响应"""
    items: List[MessageResponse]
    total: int
    page: int
    page_size: int


class UnreadCountResponse(BaseModel):
    """未读消息数响应"""
    count: int = Field(description="未读消息数量")
