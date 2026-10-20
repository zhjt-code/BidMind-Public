"""重新提取招标要求的 API 合同测试。

职责: 锁定强制重提取入口只清空提取/审查产物并复用已解析文档。
不做: 不启动真实后台任务、不访问真实数据库、不执行 LLM 提取。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api.dependencies import get_current_user  # noqa: E402
from app.api.routers import requirements as requirements_router  # noqa: E402


def test_reextract_route_is_authenticated_post_endpoint():
    """重新提取必须是登录用户 POST /reextract 入口，项目权限由 Service 校验。"""

    route = next(
        (
            item
            for item in requirements_router.router.routes
            if getattr(item, "path", "").endswith("/reextract")
        ),
        None,
    )

    assert route is not None
    assert "POST" in route.methods
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
    assert get_current_user in dependency_calls


@pytest.mark.asyncio
async def test_reextract_route_delegates_to_reextract_service(monkeypatch):
    """路由只负责调用专用 Service 并返回清理统计。"""

    calls: list[tuple[int, object]] = []
    expected = {
        "project_id": 1584,
        "status": "started",
        "queue_mode": "arq",
        "cleared": {
            "requirements": 7,
            "bid_responses": 7,
            "review_results": 7,
            "review_facts": 3,
        },
    }

    class _FakeService:
        def __init__(self, db):
            self.db = db

        async def reextract_from_parsed_documents(self, project_id, current_user):
            calls.append((project_id, current_user))
            return expected

    current_user = object()
    db = object()
    monkeypatch.setattr(
        requirements_router,
        "RequirementReextractService",
        _FakeService,
    )

    response = await requirements_router.reextract_requirements(
        project_id=1584,
        current_user=current_user,
        db=db,
    )

    assert calls == [(1584, current_user)]
    assert response.code == 200
    assert response.data == expected
    assert "重新提取" in response.message
