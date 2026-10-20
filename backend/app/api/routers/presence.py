"""在线用户状态路由"""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.presence import PresenceHeartbeatResponse
from app.services.presence_service import PresenceService


router = APIRouter(prefix="/presence", tags=["在线用户状态"])


@router.post("/heartbeat", response_model=ApiResponse[PresenceHeartbeatResponse])
async def heartbeat(
    current_user: User = Depends(get_current_user),
) -> ApiResponse[PresenceHeartbeatResponse]:
    """记录当前登录用户在线心跳"""
    result = await PresenceService().mark_user_seen(current_user.id)
    return ApiResponse(
        code=200,
        message="在线心跳已记录" if result.ok else "在线状态降级",
        data=PresenceHeartbeatResponse(
            ok=result.ok,
            degraded=result.degraded,
            online_window_seconds=result.online_window_seconds,
        ),
    )
