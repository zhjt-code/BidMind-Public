"""非唯一谓词查询的结果契约 

问题形态: 对析取/非唯一键使用 one_or_none, 多行即 500。
夹具一: 存在性 — 多行视为已占用。
夹具二: 身份查询 — 多行闭失败, 不得挑第一行。
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import AuthenticationError, ServiceError
from app.core.query_cardinality import (
    AmbiguousQueryResult,
    occupied,
    unique_scalar_or_none,
)
from app.schemas.auth import UserCreate
from app.services.auth_service import AuthService


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        if len(self._rows) > 1:
            raise AssertionError("one_or_none must not be used on a non-unique predicate")
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def scalars(self) -> _Rows:
        return _Rows(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any:
        return _Rows(self._rows).scalar_one_or_none()


def test_occupied_zero_and_one_and_many() -> None:
    assert occupied(_Result([])) is False
    assert occupied(_Result([object()])) is True
    assert occupied(_Result([object(), object()])) is True


def test_unique_scalar_or_none_rejects_many() -> None:
    assert unique_scalar_or_none(_Result([])) is None
    only = object()
    assert unique_scalar_or_none(_Result([only])) is only
    with pytest.raises(AmbiguousQueryResult):
        unique_scalar_or_none(_Result([object(), object()]))


@pytest.mark.asyncio
async def test_register_occupied_when_or_matches_two_rows() -> None:
    """本案例: username 属甲、email 属乙, 析取命中两行 → 业务占用, 不 500。"""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([object(), object()]))
    payload = UserCreate(
        username="orOwnerA",
        email="orownerb@example.com",
        password="Pass1234",
        confirm_password="Pass1234",
    )
    with pytest.raises(ServiceError) as exc_info:
        await AuthService(db).register(payload)
    assert exc_info.value.status_code == 400
    assert "已被注册" in exc_info.value.detail
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_login_disjunctive_two_rows_fails_closed() -> None:
    """同形态第二例: username==x OR email==x 命中两行时闭失败, 不 500、不挑人。"""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result([
        SimpleNamespace(id=1),
        SimpleNamespace(id=2),
    ]))
    with pytest.raises(AuthenticationError) as exc_info:
        await AuthService(db).login("collide_id", "unused")
    assert exc_info.value.status_code == 401
    assert db.commit.await_count == 0
