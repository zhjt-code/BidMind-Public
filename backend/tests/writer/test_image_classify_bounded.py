"""图片分类：LLM 段有界并发，DB 回写串行且单次 commit。

同形态双夹具：营业执照图 + 人员证书图（以及更多填充项验证峰值）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.tasks.writing_image_llm_task import (
    _MAX_CLASSIFY_CONCURRENCY,
    classify_image_items_with_llm,
    run_image_classify_batch,
)


def _item(image_id: int, title: str) -> dict[str, Any]:
    return {
        "image_id": image_id,
        "vl_content": f"识别文本-{title}",
        "material_title": title,
        "image_index": 0,
        "width": 100,
        "height": 80,
    }


class _PeakLLM:
    def __init__(self) -> None:
        self.current = 0
        self.peak = 0
        self.order: list[str] = []

    async def chat_json(self, **_kwargs):
        self.current += 1
        self.peak = max(self.peak, self.current)
        started = self.current
        await asyncio.sleep(0.04 + (0.01 * (4 - started if started <= 4 else 0)))
        self.current -= 1
        user = str(_kwargs.get("user_message") or "")
        subtype = "business_license" if "执照" in user else "id_card"
        caption = "营业执照" if "执照" in user else "人员证书"
        self.order.append(caption)
        return {
            "image_type": "license" if "执照" in user else "personnel_cert",
            "caption": caption,
            "business_subtype": subtype,
            "structured_fields": {},
        }


@pytest.mark.asyncio
async def test_classify_peak_at_most_four_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.tasks.writing_image_llm_task._raise_if_cancelled",
        lambda *_args, **_kwargs: None,
    )
    llm = _PeakLLM()
    items = [
        _item(1, "营业执照"),
        _item(2, "人员证书"),
        _item(3, "营业执照副本"),
        _item(4, "人员证书反面"),
        _item(5, "营业执照年检"),
        _item(6, "人员证书复印件"),
        _item(7, "营业执照彩色"),
        _item(8, "人员证书扫描"),
    ]
    bounded = await classify_image_items_with_llm(
        llm, items, project_id=7, concurrency=_MAX_CLASSIFY_CONCURRENCY,
    )
    llm_serial = _PeakLLM()
    serial = await classify_image_items_with_llm(
        llm_serial, items, project_id=7, concurrency=1,
    )
    assert llm.peak <= _MAX_CLASSIFY_CONCURRENCY
    assert llm_serial.peak == 1
    assert [row["image_id"] for row in bounded] == [item["image_id"] for item in items]
    assert [row["image_id"] for row in serial] == [row["image_id"] for row in bounded]
    assert [row["success"] for row in bounded] == [True] * 8


class _FakeClassifySession:
    def __init__(self, records: dict[int, SimpleNamespace]) -> None:
        self.records = records
        self.gets: list[int] = []
        self.commits = 0

    async def get(self, _model, image_id: int):
        self.gets.append(int(image_id))
        return self.records[int(image_id)]

    async def commit(self):
        self.commits += 1


class _SessionCM:
    def __init__(self, session: _FakeClassifySession) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_classify_batch_writes_in_item_order_with_single_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.tasks.writing_image_llm_task._raise_if_cancelled",
        lambda *_args, **_kwargs: None,
    )
    items = [
        _item(21, "营业执照"),
        _item(22, "人员证书"),
        _item(23, "营业执照副本"),
        _item(24, "人员证书反面"),
        _item(25, "营业执照年检"),
    ]
    records = {
        item["image_id"]: SimpleNamespace(
            image_type=None,
            caption=None,
            structured_fields=None,
            business_subtype=None,
        )
        for item in items
    }
    session = _FakeClassifySession(records)
    llm = _PeakLLM()
    monkeypatch.setattr(
        "app.core.ai.get_review_client",
        lambda **_kwargs: llm,
    )
    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        lambda: _SessionCM(session),
    )

    payload = await run_image_classify_batch({}, 7, "batch-a", items)
    assert payload["status"] == "completed"
    assert session.commits == 1
    assert session.gets == [21, 22, 23, 24, 25]
    assert llm.peak <= _MAX_CLASSIFY_CONCURRENCY
    assert [row["image_id"] for row in payload["items"]] == [21, 22, 23, 24, 25]
