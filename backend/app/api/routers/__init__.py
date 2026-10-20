"""API 路由模块

包含所有 API 端点的路由器。
"""

from app.api.routers import (
    collusion,
    feedback,
    files,
    health,
    knowledge_base,
    presence,
    projects,
    requirements,
    reviews,
    search,
    settings,
)

__all__ = ["projects", "health", "files", "search", "requirements", "reviews", "settings", "presence", "collusion", "feedback", "knowledge_base"]
