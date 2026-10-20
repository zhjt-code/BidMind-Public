"""请求级中间件

提供 Request ID 注入、日志上下文绑定等 HTTP 请求级基础设施。
"""

import re
import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_VALID_REQUEST_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
_user_id_ctx: ContextVar[int | None] = ContextVar("user_id", default=None)
# project_id: HTTP 请求级日志上下文。由 project_log_context 依赖在路径含 {project_id} 时注入,
# 与 logger.contextualize(project_id=...) 互补 (后者主要在后台任务/Core 层使用)
_project_id_ctx: ContextVar[int | None] = ContextVar("project_id", default=None)
# incident_id: 同一次失败链路的关联键, 由 RequestIdMiddleware 在请求入口生成
# task 入口可调用 set_incident_id 覆盖, 让任务级链路独立于 HTTP 请求级
_incident_id_ctx: ContextVar[str | None] = ContextVar("incident_id", default=None)


def get_request_id() -> str:
    """获取当前请求的 Request ID（无请求上下文时返回 '-'）"""
    return _request_id_ctx.get()


def get_log_user_id() -> int | None:
    """获取当前请求的已认证用户 ID（未认证时返回 None）"""
    return _user_id_ctx.get()


def set_log_user_id(user_id: int) -> None:
    """设置当前请求的已认证用户 ID，供 loguru patcher 自动注入日志"""
    _user_id_ctx.set(user_id)


def get_log_project_id() -> int | None:
    """获取当前请求级日志上下文中的 project_id（无则 None）"""
    return _project_id_ctx.get()


def set_log_project_id(project_id: int | None) -> None:
    """设置当前请求级日志上下文中的 project_id, 供 loguru patcher 自动注入。

    与 logger.contextualize 的差异:
    - 本函数面向 HTTP 请求侧, 在 Path 解析出 project_id 后写入 ContextVar
    - logger.contextualize(project_id=...) 更适合后台任务 / Core 层主动注入
    两者互不冲突: patcher 优先使用 contextualize 已写入的 extra, 缺失时回落到 ContextVar
    """
    _project_id_ctx.set(project_id)


def get_incident_id() -> str | None:
    """获取当前上下文的 incident_id（无则 None）"""
    return _incident_id_ctx.get()


def set_incident_id(incident_id: str | None) -> None:
    """设置当前上下文的 incident_id（任务入口可调用以覆盖请求级值）"""
    _incident_id_ctx.set(incident_id)


def new_incident_id() -> str:
    """生成 16 位 hex 的新 incident_id（同 uuid4 短格式，足够防碰撞）"""
    return uuid.uuid4().hex[:16]


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求注入唯一 Request ID

    - 优先使用客户端传入的 X-Request-ID 头
    - 未提供时自动生成 UUID4 短格式（前 8 位）
    - 写入 contextvars 供 loguru patcher 自动注入日志
    - 写入响应头 X-Request-ID 供前端/调试追踪
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        client_rid = request.headers.get("X-Request-ID")
        if client_rid and _VALID_REQUEST_ID.match(client_rid):
            rid = client_rid
        else:
            rid = uuid.uuid4().hex[:8]
        token = _request_id_ctx.set(rid)
        # 请求级 incident_id: 同一次 HTTP 请求触发的所有错误日志将共享此 ID
        # task 入口（worker.py / tasks/__init__.py）会用 logger.contextualize 覆盖, 形成独立链路
        incident_token = _incident_id_ctx.set(new_incident_id())
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            _incident_id_ctx.reset(incident_token)
            _request_id_ctx.reset(token)
