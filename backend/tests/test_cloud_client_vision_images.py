"""CloudClient 多模态图片消息构造防御测试。"""

from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from app.core.ai.cloud_client import CloudClient


def _image_b64(fmt: str, *, size: tuple[int, int] = (16, 16)) -> str:
    image = Image.new("RGB", size, color=(20, 40, 60))
    buf = BytesIO()
    image.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _mk_response(content: str | None = "ok", finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


def _make_client() -> CloudClient:
    return CloudClient(
        base_url="https://demo.example.com/v1",
        api_key="sk-test",
        model_name="demo-vision-model",
        timeout=30.0,
        retry_on_429=False,
    )


@pytest.mark.asyncio
async def test_chat_with_images_uses_normalized_image_data_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CloudClient 不得把真实 BMP 硬编码伪装成 image/jpeg。"""

    client = _make_client()
    captured: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _mk_response("识别完成")

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    result = await client.chat_with_images(
        text_message="请识别图片",
        image_data_list=[_image_b64("BMP")],
        max_tokens=128,
    )

    assert result == "识别完成"
    user_message = captured["messages"][0]
    image_parts = [
        part
        for part in user_message["content"]
        if part.get("type") == "image_url"
    ]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/bmp;base64,")
    assert "data:image/jpeg;base64," not in image_parts[0]["image_url"]["url"]


@pytest.mark.asyncio
async def test_chat_with_images_returns_empty_without_calling_provider_for_invalid_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """所有图片无效时应本地降级为空结果，不向 provider 发送无效多模态请求。"""

    client = _make_client()

    async def fake_create(**_kwargs: Any) -> Any:
        raise AssertionError("无有效图片时不应调用云端 provider")

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    result = await client.chat_with_images(
        text_message="请识别图片",
        image_data_list=["not valid base64"],
        max_tokens=128,
    )

    assert result == ""


@pytest.mark.asyncio
async def test_chat_with_images_skips_undersized_without_calling_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边长不足模型下限时应本地跳过，不向 provider 发送。"""

    client = _make_client()

    async def fake_create(**_kwargs: Any) -> Any:
        raise AssertionError("过小图片不应调用云端 provider")

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    result = await client.chat_with_images(
        text_message="请识别图片",
        image_data_list=[_image_b64("PNG", size=(7, 100))],
        max_tokens=128,
    )

    assert result == ""
