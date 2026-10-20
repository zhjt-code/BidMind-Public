""" P1-3: 高风险核验窗口并发契约。

锁定: 并发度受限、结果按窗口原序、单窗失败不传染、进度单调。
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.core.extractor.mandatory.atomic_extract import extract_atomic_conditions
from app.core.extractor.mandatory.contracts import PageText, ScopeWindow


def _pages_and_windows(n: int = 4):
    pages = [
        PageText(page_num=i, text=f"须具备条件{i}，否则投标无效")
        for i in range(1, n + 1)
    ]
    windows = tuple(ScopeWindow(pages=(i,), reason="heading") for i in range(1, n + 1))
    return pages, windows


@pytest.mark.asyncio
async def test_window_concurrency_is_capped() -> None:
    inflight = 0
    peak = 0
    gate = asyncio.Event()
    started = 0

    class _FakeClient:
        async def chat_json(self, **kwargs):
            nonlocal inflight, peak, started
            inflight += 1
            peak = max(peak, inflight)
            started += 1
            if started >= 2:
                gate.set()
            await gate.wait()
            await asyncio.sleep(0)
            inflight -= 1
            return [{
                "description": "须具备安全生产许可证，否则投标无效",
                "category": "QUAL",
                "source_pages": [1],
                "keywords": ["许可"],
            }]

    pages, windows = _pages_and_windows(4)
    with patch(
        "app.core.extractor.mandatory.atomic_extract.get_review_concurrency",
        return_value=2,
    ):
        await extract_atomic_conditions(
            _FakeClient(),
            pages=pages,
            windows=windows,
        )
    assert peak <= 2
    assert peak >= 2


@pytest.mark.asyncio
async def test_results_preserve_window_order() -> None:
    class _FakeClient:
        async def chat_json(self, **kwargs):
            user = kwargs["user_message"]
            if "条件1" in user:
                await asyncio.sleep(0.05)
                desc = "第一条窗口条件必须独立保留否则废标"
            elif "条件2" in user:
                desc = "第二条窗口条件必须独立保留否则废标"
            elif "条件3" in user:
                desc = "第三条窗口条件必须独立保留否则废标"
            else:
                desc = "第四条窗口条件必须独立保留否则废标"
            return [{
                "description": desc,
                "category": "QUAL",
                "source_pages": [1],
                "keywords": ["条件"],
            }]

    pages, windows = _pages_and_windows(4)
    with patch(
        "app.core.extractor.mandatory.atomic_extract.get_review_concurrency",
        return_value=4,
    ):
        items = await extract_atomic_conditions(
            _FakeClient(),
            pages=pages,
            windows=windows,
        )
    assert [item.description[:2] for item in items] == ["第一", "第二", "第三", "第四"]


@pytest.mark.asyncio
async def test_single_window_failure_does_not_infect() -> None:
    class _FakeClient:
        async def chat_json(self, **kwargs):
            if "条件2" in kwargs["user_message"]:
                raise RuntimeError("window boom")
            return [{
                "description": "须具备安全生产许可证否则废标",
                "category": "QUAL",
                "source_pages": [1],
                "keywords": ["许可"],
            }]

    pages, windows = _pages_and_windows(3)
    with patch(
        "app.core.extractor.mandatory.atomic_extract.get_review_concurrency",
        return_value=3,
    ):
        items = await extract_atomic_conditions(
            _FakeClient(),
            pages=pages,
            windows=windows,
        )
    assert len(items) == 1
    assert items[0].description.startswith("须具备安全生产许可证")


@pytest.mark.asyncio
async def test_progress_is_monotonic() -> None:
    seen: list[int] = []

    class _FakeClient:
        async def chat_json(self, **_kwargs):
            await asyncio.sleep(0.01)
            return [{
                "description": "须具备安全生产许可证否则废标",
                "category": "QUAL",
                "source_pages": [1],
                "keywords": ["许可"],
            }]

    def _cb(stage, current, total, message, extra=None):
        seen.append(current)

    pages, windows = _pages_and_windows(3)
    with patch(
        "app.core.extractor.mandatory.atomic_extract.get_review_concurrency",
        return_value=3,
    ):
        await extract_atomic_conditions(
            _FakeClient(),
            pages=pages,
            windows=windows,
            progress_callback=_cb,
        )
    assert seen[0] == 0
    assert seen[-1] == 3
    assert seen == sorted(seen)
