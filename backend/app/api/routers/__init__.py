"""API 路由模块

包含所有 API 端点的路由器。
"""

from app.api.routers import projects, health, files, search, requirements, reviews, settings, presence, collusion, feedback, knowledge_base

__all__ = ["projects", "health", "files", "search", "requirements", "reviews", "settings", "presence", "collusion", "feedback", "knowledge_base"]
