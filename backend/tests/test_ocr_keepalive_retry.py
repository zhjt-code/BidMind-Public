""" 防御测试: OCR keep-alive 陈旧连接竞态的重试与配置契约。

风险映射: uvicorn 与 httpx 两侧闲置超时同吃 5s 默认值, 空档略超 5s 时客户端
复用刚被服务端关闭的连接 -> ReadError('') -> 该页 OCR 结果静默丢失。
核心断言: (1) 陈旧连接错误换新连接重试一次且仅一次; (2) 超时/连接失败不重试
(保留既有实例摘除语义); (3) 客户端 keepalive_expiry 必须严格小于服务端窗口。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import pytest

from app.core.parser import ocr_engine
from app.core.parser.ocr_engine import (
    _CLIENT_KEEPALIVE_EXPIRY_SEC,
    _post_predict_with_stale_retry,
    OcrEngine,
)


@pytest.mark.asyncio
async def test_stale_read_error_retried_once_and_succeeds() -> None:
    """ 核心: 首次 ReadError(陈旧连接) -> 换新连接重试一次并返回成功响应。"""
    ok_response = Mock()
    client = AsyncMock()
    client.post = AsyncMock(side_effect=[httpx.ReadError(""), ok_response])

    resp = await _post_predict_with_stale_retry(client, "img-b64", "http://ocr-2:9000")

    assert resp is ok_response
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_stale_error_gives_up_after_second_failure() -> None:
    """重试仅一次: 第二次仍失败则原样抛出, 交由调用方兜底分支返回空结果。"""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=[httpx.ReadError(""), httpx.ReadError("")])

    with pytest.raises(httpx.ReadError):
        await _post_predict_with_stale_retry(client, "img-b64", "http://ocr-2:9000")
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_remote_protocol_error_is_retriable() -> None:
    """RemoteProtocolError(服务端无响应断开)同属陈旧连接特征, 必须可重试。"""
    ok_response = Mock()
    client = AsyncMock()
    client.post = AsyncMock(
        side_effect=[httpx.RemoteProtocolError("Server disconnected"), ok_response]
    )

    resp = await _post_predict_with_stale_retry(client, "img-b64", "http://ocr-2:9000")

    assert resp is ok_response
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_timeout_not_retried() -> None:
    """超时不重试: 必须原样抛出, 走调用方 TimeoutException 分支摘除实例。"""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(httpx.ReadTimeout):
        await _post_predict_with_stale_retry(client, "img-b64", "http://ocr-2:9000")
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_connect_error_not_retried() -> None:
    """连接失败不重试: 实例可能真不可达, 保留 ConnectError 分支的摘除语义。"""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        await _post_predict_with_stale_retry(client, "img-b64", "http://ocr-2:9000")
    assert client.post.await_count == 1


def test_pool_client_sets_keepalive_expiry(monkeypatch) -> None:
    """_get_pool_client 必须显式传入 keepalive_expiry, 禁止回落 httpx 5s 默认值。"""
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    engine = OcrEngine(service_urls=["http://ocr-1:9000"])
    engine._get_pool_client(0)

    assert "limits" in captured
    assert captured["limits"].keepalive_expiry == _CLIENT_KEEPALIVE_EXPIRY_SEC


def test_client_keepalive_expiry_strictly_below_server_window() -> None:
    """配置契约: 客户端空闲过期必须严格小于 ocr/Dockerfile 的 --timeout-keep-alive。

 防止未来任一侧单独调整导致竞态窗口重新出现(教训)。
 """
    dockerfile = Path(__file__).resolve().parents[2] / "ocr" / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")
    match = re.search(r'"--timeout-keep-alive",\s*"(\d+)"', content)

    assert match, "ocr/Dockerfile 必须显式声明 --timeout-keep-alive, 禁止依赖 uvicorn 5s 默认值"
    server_window = int(match.group(1))
    assert _CLIENT_KEEPALIVE_EXPIRY_SEC < server_window
