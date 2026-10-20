"""调试字段仅管理员可开：结果查询与报告生成是同形态两入口。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.exceptions import ServiceError
from app.models.user import UserRole
from app.services.review_service import ReviewService


class _GateProbeDb:
    """闸门之后才会访问数据库。"""

    def __init__(self) -> None:
        self.execute_calls = 0

    async def execute(self, _stmt: Any) -> Any:
        self.execute_calls += 1
        raise RuntimeError("past-gate")


def _user(user_id: int, role: UserRole) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        role=role,
        is_admin=role == UserRole.ADMIN,
    )


async def _project(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(id=1)


@pytest.mark.asyncio
async def test_include_debug_results_rejected_for_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _GateProbeDb()
    service = ReviewService(db)
    monkeypatch.setattr(service, "_get_project_or_raise", _project)

    with pytest.raises(ServiceError) as exc:
        await service.get_review_results(1, _user(7, UserRole.USER), include_debug=True)

    assert exc.value.status_code == 403
    assert "管理员" in exc.value.detail
    assert db.execute_calls == 0


@pytest.mark.asyncio
async def test_include_debug_report_rejected_for_other_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同形态第二例：另一用户走报告生成入口，同样拒绝调试字段。"""
    db = _GateProbeDb()
    service = ReviewService(db)
    monkeypatch.setattr(service, "_get_project_or_raise", _project)

    with pytest.raises(ServiceError) as exc:
        await service.generate_report(1, _user(19, UserRole.USER), include_debug=True)

    assert exc.value.status_code == 403
    assert db.execute_calls == 0


@pytest.mark.asyncio
async def test_include_debug_allowed_for_admin_and_default_off_for_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _GateProbeDb()
    service = ReviewService(db)
    monkeypatch.setattr(service, "_get_project_or_raise", _project)

    with pytest.raises(RuntimeError, match="past-gate"):
        await service.get_review_results(1, _user(1, UserRole.ADMIN), include_debug=True)

    with pytest.raises(RuntimeError, match="past-gate"):
        await service.get_review_results(1, _user(7, UserRole.USER), include_debug=False)

    assert db.execute_calls == 2
