""" 防御测试: with_db_reconnect 装饰器与死连接判定。

四个核心场景:
1. 首次抛 "connection is closed" InterfaceError, 第二次成功 -> 装饰器透明重试
2. 连续两次抛同样异常 -> 重试耗尽后向上抛出, 调用次数 = max_retries + 1
3. 非 "connection is closed" 文案的 InterfaceError -> 不重试, 直接 raise
4. 业务异常 (ValueError) -> 不重试, 直接 raise

测试不依赖真实 DB 连接, 纯 mock 异常对象。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import InterfaceError as SAInterfaceError

from app.core.database import is_dead_connection_error, with_db_reconnect


def _dead_connection_error() -> SAInterfaceError:
    """构造与生产报错完全同形的 InterfaceError。"""
    return SAInterfaceError(
        statement="SELECT chunks.id FROM chunks WHERE chunks.document_id = $1",
        params=(1701,),
        orig=Exception(
            "<class 'asyncpg.exceptions._base.InterfaceError'>: "
            "cannot call PreparedStatement.fetch(): the underlying connection is closed"
        ),
    )


def _alive_interface_error() -> SAInterfaceError:
    """构造一个非 "connection is closed" 的 InterfaceError, 不应被重试。"""
    return SAInterfaceError(
        statement="SELECT 1",
        params=(),
        orig=Exception("some other interface error: invalid protocol"),
    )


@pytest.mark.asyncio
async def test_retries_once_on_dead_connection_then_succeeds() -> None:
    """首次抛 connection is closed 异常, 第二次成功返回结果。"""
    side_effects: list[Any] = [_dead_connection_error(), "ok"]
    mock_fn = AsyncMock(side_effect=side_effects)

    @with_db_reconnect(max_retries=1, retry_delay=0.0)
    async def target(*args: Any, **kwargs: Any) -> Any:
        return await mock_fn(*args, **kwargs)

    result = await target("arg1", key="val")

    assert result == "ok"
    assert mock_fn.await_count == 2
    mock_fn.assert_any_await("arg1", key="val")


@pytest.mark.asyncio
async def test_raises_after_retries_exhausted() -> None:
    """连续 max_retries+1 次抛同样异常, 最终向上抛出。"""
    errors = [_dead_connection_error(), _dead_connection_error()]
    mock_fn = AsyncMock(side_effect=errors)

    @with_db_reconnect(max_retries=1, retry_delay=0.0)
    async def target() -> None:
        await mock_fn()

    with pytest.raises(SAInterfaceError) as exc_info:
        await target()

    assert "connection is closed" in str(exc_info.value).lower()
    assert mock_fn.await_count == 2


@pytest.mark.asyncio
async def test_does_not_retry_on_non_matching_interface_error() -> None:
    """InterfaceError 但不含 connection is closed 文案 -> 不重试, 立即 raise。"""
    error = _alive_interface_error()
    mock_fn = AsyncMock(side_effect=error)

    @with_db_reconnect(max_retries=1, retry_delay=0.0)
    async def target() -> None:
        await mock_fn()

    with pytest.raises(SAInterfaceError):
        await target()

    assert mock_fn.await_count == 1


@pytest.mark.asyncio
async def test_does_not_retry_on_business_exception() -> None:
    """业务异常 (ValueError) -> 不重试, 立即 raise, 保留原异常类型。"""
    mock_fn = AsyncMock(side_effect=ValueError("业务参数错误"))

    @with_db_reconnect(max_retries=1, retry_delay=0.0)
    async def target() -> None:
        await mock_fn()

    with pytest.raises(ValueError) as exc_info:
        await target()

    assert "业务参数错误" in str(exc_info.value)
    assert mock_fn.await_count == 1


def test_is_dead_connection_error_classification() -> None:
    """is_dead_connection_error 必须准确区分三类异常。"""
    assert is_dead_connection_error(_dead_connection_error()) is True
    assert is_dead_connection_error(_alive_interface_error()) is False
    assert is_dead_connection_error(ValueError("foo")) is False
    assert is_dead_connection_error(RuntimeError("connection is closed")) is False, (
        "非 InterfaceError 的同文案异常不应被识别为死连接"
    )


@pytest.mark.asyncio
async def test_decorator_respects_max_retries_zero() -> None:
    """max_retries=0 时即使死连接也不重试, 仅允许一次尝试。"""
    mock_fn = AsyncMock(side_effect=_dead_connection_error())

    @with_db_reconnect(max_retries=0, retry_delay=0.0)
    async def target() -> None:
        await mock_fn()

    with pytest.raises(SAInterfaceError):
        await target()

    assert mock_fn.await_count == 1


@pytest.mark.asyncio
async def test_retry_delay_is_awaited(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试前应 await asyncio.sleep(retry_delay), 给 pool 时间替换死连接。"""
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    mock_fn = AsyncMock(side_effect=[_dead_connection_error(), "ok"])

    @with_db_reconnect(max_retries=1, retry_delay=0.5)
    async def target() -> None:
        return await mock_fn()

    result = await target()
    assert result == "ok"
    assert sleep_calls == [0.5]
