"""P0-B: 真实健康探测防御测试。

锁定契约:
- 视觉探测必须发送真实图片(image_data_list 非空), 文本 ping 不能证明视觉可用;
- 空正文 / 纯 think / finish_reason=length/content_filter 一律判失败;
- run_primary_probe 按用途路由: vision -> 图片探测, review/judge -> 文本探测;
- 探测流量标记 admin_test, 不污染生产统计;
- strict schema 探测能把 UnsupportedResponseFormatError 归为能力缺失而非异常。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

from app.core.ai.attempt_recorder import current_traffic_class
from app.core.ai.cloud_client import UnsupportedResponseFormatError
from app.core.ai.health_probe import (
    probe_json_object,
    probe_strict_schema,
    probe_text_basic,
    probe_vision_basic,
    run_primary_probe,
)


class _FakeProbeClient:
    def __init__(
        self,
        *,
        chat_result: Any = "确认",
        vision_result: Any = "主色调是红色",
        json_result: Any = None,
        finish_reason: Optional[str] = "stop",
    ) -> None:
        self.model_name = "probe-model"
        self._chat_result = chat_result
        self._vision_result = vision_result
        self._json_result = json_result if json_result is not None else {"ok": True}
        self._last_finish_reason = finish_reason
        self.chat_calls = 0
        self.vision_calls = 0
        self.seen_traffic: Optional[str] = None
        self.seen_images: Optional[list] = None

    async def chat(self, **kwargs: Any) -> Any:
        self.chat_calls += 1
        self.seen_traffic = current_traffic_class.get()
        if isinstance(self._chat_result, Exception):
            raise self._chat_result
        return self._chat_result

    async def chat_json(self, **kwargs: Any) -> Any:
        self.seen_traffic = current_traffic_class.get()
        if isinstance(self._json_result, Exception):
            raise self._json_result
        return self._json_result

    async def chat_with_images(
        self, *, image_data_list: list, **kwargs: Any,
    ) -> Any:
        self.vision_calls += 1
        self.seen_images = image_data_list
        self.seen_traffic = current_traffic_class.get()
        if isinstance(self._vision_result, Exception):
            raise self._vision_result
        return self._vision_result


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------- 文本探测 ----------

def test_text_probe_ok_on_non_empty() -> None:
    report = _run(probe_text_basic(_FakeProbeClient(chat_result="确认")))
    assert report.ok is True
    assert report.kind == "text_basic"


@pytest.mark.parametrize("content", ["", "   ", None])
def test_text_probe_fails_on_empty(content: Optional[str]) -> None:
    report = _run(probe_text_basic(_FakeProbeClient(chat_result=content)))
    assert report.ok is False
    assert "空正文" in (report.error or "")


def test_text_probe_fails_on_length_truncation() -> None:
    client = _FakeProbeClient(chat_result="被截断的部分回", finish_reason="length")
    report = _run(probe_text_basic(client))
    assert report.ok is False, "finish_reason=length 不是健康状态( 语义)"
    assert "length" in (report.error or "")


def test_text_probe_fails_on_content_filter() -> None:
    client = _FakeProbeClient(chat_result="有内容", finish_reason="content_filter")
    report = _run(probe_text_basic(client))
    assert report.ok is False


def test_text_probe_fails_on_exception() -> None:
    report = _run(probe_text_basic(_FakeProbeClient(chat_result=RuntimeError("conn refused"))))
    assert report.ok is False
    assert "conn refused" in (report.error or "")


# ---------- 视觉探测 ----------

def test_vision_probe_sends_real_image() -> None:
    client = _FakeProbeClient()
    report = _run(probe_vision_basic(client))
    assert report.ok is True
    assert client.vision_calls == 1
    assert client.seen_images and len(client.seen_images) == 1
    assert len(client.seen_images[0]) > 50, "必须携带真实 base64 图片数据"


def test_vision_probe_fails_on_empty_response() -> None:
    client = _FakeProbeClient(vision_result="")
    report = _run(probe_vision_basic(client))
    assert report.ok is False
    assert "空正文" in (report.error or "")


# ---------- JSON / strict 探测 ----------

def test_json_probe_ok_and_bad_type() -> None:
    assert _run(probe_json_object(_FakeProbeClient(json_result={"ok": True}))).ok is True
    bad = _run(probe_json_object(_FakeProbeClient(json_result="纯文本")))
    assert bad.ok is False


def test_strict_probe_marks_capability_missing() -> None:
    client = _FakeProbeClient(
        json_result=UnsupportedResponseFormatError("response_format unavailable"),
    )
    report = _run(probe_strict_schema(client))
    assert report.ok is False
    assert "不支持" in (report.error or "")


def test_strict_probe_ok_on_schema_reply() -> None:
    client = _FakeProbeClient(json_result={"ok": True, "note": "probe"})
    report = _run(probe_strict_schema(client))
    assert report.ok is True


# ---------- 用途路由与流量标记 ----------

def test_primary_probe_routes_vision_to_image() -> None:
    client = _FakeProbeClient()
    report = _run(run_primary_probe(client, purpose="vision"))
    assert report.kind == "vision_basic"
    assert client.vision_calls == 1
    assert client.chat_calls == 0


def test_primary_probe_routes_review_to_text() -> None:
    client = _FakeProbeClient()
    report = _run(run_primary_probe(client, purpose="review"))
    assert report.kind == "text_basic"
    assert client.chat_calls == 1
    assert client.vision_calls == 0


def test_probe_traffic_marked_admin_test() -> None:
    client = _FakeProbeClient()
    _run(probe_text_basic(client))
    assert client.seen_traffic == "admin_test", "探测流量必须标记 admin_test"


def test_probe_timeout_reported() -> None:
    class _Hang(_FakeProbeClient):
        async def chat(self, **kwargs: Any) -> Any:
            await asyncio.sleep(30)
            return "late"

    report = _run(probe_text_basic(_Hang(), timeout=0.2))
    assert report.ok is False
    assert "超时" in (report.error or "")
