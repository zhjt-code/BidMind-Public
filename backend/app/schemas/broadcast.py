"""广播站内信相关的 Pydantic Schema

定义管理员侧 CRUD 与用户侧 Banner / 状态变更的数据结构。
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class BroadcastCreate(BaseModel):
    """管理员创建广播的请求体"""
    level: str = Field(
        default="info",
        pattern="^(info|success|warning|critical)$",
        description="广播级别"
    )
    title: str = Field(..., min_length=1, max_length=200, description="标题")
    content: str = Field(..., min_length=1, max_length=2000, description="正文")
    ref_url: Optional[str] = Field(
        None, max_length=500,
        description="点击横幅跳转的内部路径，可空"
    )
    show_banner: bool = Field(default=True, description="是否在顶部 Banner 展示")
    dismissible: bool = Field(default=True, description="Banner 是否可被用户关闭")
    starts_at: Optional[datetime] = Field(
        None, description="生效时间，留空则立即生效"
    )
    ends_at: Optional[datetime] = Field(
        None, description="失效时间，留空则长期有效"
    )

    @model_validator(mode="after")
    def _check_time_window(self) -> "BroadcastCreate":
        """生效时间必须早于失效时间。"""
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("生效时间必须早于失效时间")
        return self


class BroadcastUpdate(BaseModel):
    """管理员更新广播的请求体（所有字段可选）"""
    level: Optional[str] = Field(
        None, pattern="^(info|success|warning|critical)$",
        description="广播级别"
    )
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, min_length=1, max_length=2000)
    ref_url: Optional[str] = Field(None, max_length=500)
    show_banner: Optional[bool] = None
    dismissible: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class BroadcastCreatorInfo(BaseModel):
    """广播创建者简要信息"""
    id: int
    username: str


class BroadcastResponse(BaseModel):
    """管理员侧的广播完整响应"""
    id: int
    level: str
    title: str
    content: str
    ref_url: Optional[str] = None
    show_banner: bool
    dismissible: bool
    starts_at: datetime
    ends_at: Optional[datetime] = None
    created_by: Optional[int] = None
    creator: Optional[BroadcastCreatorInfo] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BroadcastListResponse(BaseModel):
    """广播列表响应（管理员侧）"""
    items: List[BroadcastResponse]
    total: int
    page: int
    page_size: int


class BannerItem(BaseModel):
    """用户侧 Banner 横幅响应（轻量）

    与 BroadcastResponse 的差异：
    - 不暴露 created_by / deleted_at 等管理字段
    - 包含当前用户的 read 状态（不展示 dismissed -- 拉到的本身就是未关闭）
    """
    id: int
    level: str
    title: str
    content: str
    ref_url: Optional[str] = None
    dismissible: bool
    is_read: bool = Field(description="当前用户是否已读")
    created_at: datetime


class BannerListResponse(BaseModel):
    """Banner 列表响应"""
    items: List[BannerItem]
