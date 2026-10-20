"""Requirement API 合同防御测试。

职责: 锁定确认页所需的评分项满分校验报告必须从 Service 透传到 API 响应。
不做: 不启动 FastAPI 测试客户端、不访问真实数据库; 只验证路由装配合同。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api.routers import requirements as requirements_router  # noqa: E402

pytestmark = pytest.mark.scoring


@pytest.mark.asyncio
async def test_list_requirements_returns_scoring_totals_report(monkeypatch):
    """: 路由不能丢弃 Service 返回的 scoring_totals_report。"""

    expected_report = {
        "needs_review": False,
        "note": "按卷满分校验通过",
        "by_volume": {"technical": 100.0},
        "volume_checks": {
            "technical": {"sum": 100.0, "expected": 100.0, "weight": 0.4, "within": True}
        },
        "weight_sum_ok": False,
        "item_count": 16,
        "missing_score_count": 0,
    }

    class _FakeRequirementService:
        def __init__(self, _db):
            pass

        async def list_requirements(self, *_args, **_kwargs):
            return {
                "total": 0,
                "confirmed_count": 0,
                "pending_count": 0,
                "items": [],
                "scoring_totals_report": expected_report,
            }

    monkeypatch.setattr(requirements_router, "RequirementService", _FakeRequirementService)

    response = await requirements_router.list_requirements(
        project_id=1502,
        confirmed=None,
        category=None,
        current_user=object(),
        db=object(),
    )

    assert response.scoring_totals_report == expected_report


# ===== score_weight 满分修正合同测试 =====

def test_requirement_update_accepts_score_weight():
    """RequirementUpdate 模型必须接受 score_weight 字段（P2 C1 满分修正）。"""
    from app.schemas.requirement import RequirementUpdate
    update = RequirementUpdate(score_weight=20.0)
    assert update.score_weight == 20.0


def test_requirement_update_score_weight_none_by_default():
    """score_weight 默认 None，不覆盖既有分值（兼容已有接口调用方）。"""
    from app.schemas.requirement import RequirementUpdate
    update = RequirementUpdate(is_mandatory=True)
    assert update.score_weight is None


def test_requirement_update_score_weight_zero_rejected():
    """score_weight <= 0 应被验证拒绝（评分项满分不得为零或负数）。"""
    import pytest
    from pydantic import ValidationError

    from app.schemas.requirement import RequirementUpdate
    with pytest.raises(ValidationError):
        RequirementUpdate(score_weight=0.0)


def test_sync_score_weight_to_metadata_updates_max_score():
    """_sync_score_weight_to_metadata 应将 max_score 更新为新满分，保留其他字段。"""
    from app.services.requirement_service import _sync_score_weight_to_metadata
    meta = {"score_item_name": "技术方案", "max_score": 30.0, "volume": "technical"}
    result = _sync_score_weight_to_metadata(15.0, meta)
    assert result["max_score"] == 15.0
    assert result["score_item_name"] == "技术方案"
    assert result["volume"] == "technical"


def test_sync_score_weight_to_metadata_none_meta():
    """scoring_metadata 为 None 时，应创建仅含 max_score 的新 dict。"""
    from app.services.requirement_service import _sync_score_weight_to_metadata
    result = _sync_score_weight_to_metadata(25.0, None)
    assert result["max_score"] == 25.0
