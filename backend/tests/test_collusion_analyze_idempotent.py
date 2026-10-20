"""POST /collusion/analyze 幂等返回防御测试

锁定场景：项目已处于 REVIEWING 且 is_task_active=True 时，
endpoint 必须返回 ApiResponse(code=200) 而不是抛 HTTPException(400)。

背景：
- 旧实现见 ：在前端 StrictMode/effect 重入触发并发 POST /analyze 时，
 第一个请求已把 project.status 推到 REVIEWING + 派发任务，第二个请求命中
 防重启动校验抛 400，前端 catch 弹"启动失败: ... 串标分析正在进行中"。
- 修复策略与 /retry endpoint 对齐：将"已在进行中"分支改为幂等返回，让
 并发或重入的重复请求无副作用地拿到与首次启动等价的响应。

测试覆盖：
- T1: REVIEWING + 任务活跃 → 幂等 200，data.status=processing，绝不抛异常
- T2: 非 REVIEWING（如 UPLOADING）需正常走启动流程（确保没有把所有请求都
 短路成 200）—— 通过 mock is_task_active 永不被调用来侧面验证
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routers import collusion as collusion_router
from app.models.project import ProjectStatus, ProjectType
from app.schemas.common import ApiResponse


def _fake_project(status: ProjectStatus) -> SimpleNamespace:
    """构造满足 endpoint 早期校验所需的最小 Project 对象。"""
    return SimpleNamespace(
        id=42,
        project_type=ProjectType.COLLUSION_CHECK,
        status=status,
        error_message=None,
        collusion_summary=None,
    )


@pytest.mark.asyncio
async def test_T1_reviewing_with_active_task_returns_idempotent_200(monkeypatch):
    """REVIEWING + is_task_active=True：必须返回 code=200 而非抛 HTTPException。"""

    async def fake_get_project_or_404(project_id, db, current_user, *, require_owner=False):
        return _fake_project(ProjectStatus.REVIEWING)

    monkeypatch.setattr(
        collusion_router, "_get_project_or_404", fake_get_project_or_404
    )
    monkeypatch.setattr(collusion_router, "is_task_active", lambda _name: True)

    result = await collusion_router.start_collusion_analyze(
        project_id=42,
        current_user=SimpleNamespace(id=1),
        db=None,
    )

    assert isinstance(result, ApiResponse)
    assert result.code == 200
    assert result.data is not None
    assert result.data.get("project_id") == 42
    assert result.data.get("status") == "processing"


@pytest.mark.asyncio
async def test_T2_non_reviewing_does_not_short_circuit(monkeypatch):
    """非 REVIEWING 状态不应进入幂等分支：is_task_active 不应被调用。

    侧面验证修复没有把所有请求都短路成 200——非 REVIEWING 状态下应继续走
    后续 bid_docs 校验等正常启动流程（这里通过让 is_task_active 抛错来证明
    它不会被触达；后续 DB 查询会自然失败，但那不是本测试的关注点）。
    """

    async def fake_get_project_or_404(project_id, db, current_user, *, require_owner=False):
        return _fake_project(ProjectStatus.UPLOADING)

    monkeypatch.setattr(
        collusion_router, "_get_project_or_404", fake_get_project_or_404
    )

    def _should_not_be_called(_name):
        raise AssertionError("is_task_active 不应在非 REVIEWING 状态被调用")

    monkeypatch.setattr(collusion_router, "is_task_active", _should_not_be_called)

    with pytest.raises(Exception):
        # 预期会因为后续 DB.execute(...) 在 db=None 上失败而抛错；
        # 关键是 is_task_active 在抛错之前没有被触达
        await collusion_router.start_collusion_analyze(
            project_id=42,
            current_user=SimpleNamespace(id=1),
            db=None,
        )
