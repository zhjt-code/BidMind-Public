"""在线用户状态 Schema"""

from pydantic import BaseModel, Field


class PresenceHeartbeatResponse(BaseModel):
    """在线心跳响应"""

    ok: bool = Field(description="心跳是否成功写入")
    degraded: bool = Field(description="在线状态是否处于降级模式")
    online_window_seconds: int = Field(description="在线判定窗口秒数")


class OnlineStatsResponse(BaseModel):
    """在线用户统计响应"""

    online_count: int = Field(description="在线用户数")
    online_window_seconds: int = Field(description="在线判定窗口秒数")
    degraded: bool = Field(description="在线状态是否处于降级模式")
