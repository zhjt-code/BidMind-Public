"""文档图片 LLM 子任务 修复防御测试。

防御目标:
- 心跳保活: wait_for_document_image_llm_result 在 redis polling 期间必须按
 keepalive_interval 周期触发 callback, 完成或超时后立即停止。
- 回调隔离: callback 抛异常不得污染 wait 主流程。
- batch 并发: run_document_image_vl_batch 必须 asyncio.gather 并发执行 N 个
 items, 而非串行 for-await; 单 item 失败不能拖垮整批; 结果顺序与入参一致。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.tasks.document_image_llm_task import (  # noqa: E402
    full_page_to_vl_item,
    image_to_vl_item,
    make_image_key,
    result_key,
    run_document_image_vl_batch,
    wait_for_document_image_llm_result,
)


@dataclass
class _FakeImage:
    page_num: int = 1
    image_index: int = 0
    width: int = 120
    height: int = 120
    file_path: str = "/tmp/test.png"
    spatial_context: str = ""
    visual_content: str = ""
    image_format: str = "png"
    xref: int = 0
    bbox: tuple[int, int, int, int] | None = None

    @property
    def size_category(self) -> str:
        return "小图标"


class _FakeRedis:
    """支持 setex / get / 延迟写入的轻量 redis 模拟。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def setex(self, key: str, _ttl: int, value: str) -> bool:
        self._data[key] = value
        return True

    async def get(self, key: str):
        return self._data.get(key)

    def write_after(self, key: str, payload: dict, delay_sec: float):
        """N 秒后写入 key, 用于模拟 LlmWorker 完成回写。"""

        async def _later():
            await asyncio.sleep(delay_sec)
            self._data[key] = json.dumps(payload)

        return asyncio.create_task(_later())


# ============================================================================
# 方案 1 防御测试: keepalive_callback
# ============================================================================


_REDIS_POOL_PATCH = "app.core.redis.get_redis_pool"


@pytest.mark.asyncio
async def test_keepalive_fires_periodically_during_wait() -> None:
    """LlmWorker 处理慢时, keepalive_callback 必须按 interval 周期触发。"""
    redis = _FakeRedis()
    batch_id = "batch-keepalive-1"
    payload = {"status": "completed", "items": []}
    redis.write_after(result_key(batch_id), payload, delay_sec=2.5)

    tick_times: list[float] = []
    start = time.monotonic()

    def _on_tick() -> None:
        tick_times.append(time.monotonic() - start)

    with patch(_REDIS_POOL_PATCH, new=AsyncMock(return_value=redis)):
        result = await wait_for_document_image_llm_result(
            batch_id,
            poll_interval=0.05,
            keepalive_callback=_on_tick,
            keepalive_interval=0.5,
        )

    assert result["status"] == "completed"
    # 2.5s 等待 / 0.5s 间隔 -> 至少 4 次, 至多 6 次(允许调度抖动)
    assert 4 <= len(tick_times) <= 6, f"keepalive tick 异常: {tick_times}"
    # 每次相邻间隔 >= 0.4s (容忍 0.1s 抖动)
    for i in range(1, len(tick_times)):
        assert tick_times[i] - tick_times[i - 1] >= 0.4


@pytest.mark.asyncio
async def test_keepalive_stops_after_result_returned() -> None:
    """结果返回后 keepalive_callback 必须立即停止, 不能再触发。"""
    redis = _FakeRedis()
    batch_id = "batch-stop-1"
    payload = {"status": "completed", "items": []}
    redis.write_after(result_key(batch_id), payload, delay_sec=0.3)

    tick_count = 0

    def _on_tick() -> None:
        nonlocal tick_count
        tick_count += 1

    with patch(_REDIS_POOL_PATCH, new=AsyncMock(return_value=redis)):
        await wait_for_document_image_llm_result(
            batch_id,
            poll_interval=0.05,
            keepalive_callback=_on_tick,
            keepalive_interval=0.1,
        )

    snapshot = tick_count
    # 等待 1 秒, 验证返回后再无 tick 触发
    await asyncio.sleep(1.0)
    assert tick_count == snapshot, "wait 返回后 keepalive 仍在触发"


@pytest.mark.asyncio
async def test_keepalive_callback_exception_does_not_break_wait() -> None:
    """callback 抛异常必须被吞掉, wait 主流程要正常完成。"""
    redis = _FakeRedis()
    batch_id = "batch-exc-1"
    payload = {"status": "completed", "items": [{"image_key": "k", "content": "ok"}]}
    redis.write_after(result_key(batch_id), payload, delay_sec=0.6)

    call_count = 0

    def _bad_callback() -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("intentional callback failure")

    with patch(_REDIS_POOL_PATCH, new=AsyncMock(return_value=redis)):
        result = await wait_for_document_image_llm_result(
            batch_id,
            poll_interval=0.05,
            keepalive_callback=_bad_callback,
            keepalive_interval=0.2,
        )

    assert result["status"] == "completed"
    assert result["items"][0]["content"] == "ok"
    # callback 至少被尝试调用过一次, 但异常没让 wait 失败
    assert call_count >= 1


@pytest.mark.asyncio
async def test_keepalive_no_callback_backwards_compatible() -> None:
    """不传 callback 时旧调用方行为完全不变(向后兼容)。"""
    redis = _FakeRedis()
    batch_id = "batch-compat-1"
    payload = {"status": "completed", "items": []}
    redis.write_after(result_key(batch_id), payload, delay_sec=0.3)

    with patch(_REDIS_POOL_PATCH, new=AsyncMock(return_value=redis)):
        result = await wait_for_document_image_llm_result(
            batch_id,
            poll_interval=0.05,
        )

    assert result["status"] == "completed"


# ============================================================================
# 方案 2 防御测试: run_document_image_vl_batch 并发执行
# ============================================================================


@pytest.mark.asyncio
async def test_batch_items_run_concurrently_not_serially() -> None:
    """多 items 必须并发执行, 总耗时接近单 item 而非 N 倍。

    用 asyncio.Event 互等: 串行实现下第 2 个 item 永远等不到第 N 个 item
    的启动信号, 触发 wait_for 超时, 测试失败。
    """
    redis = _FakeRedis()
    items = [
        full_page_to_vl_item(
            image_key=f"page-{i}",
            page_b64=f"b64-{i}",
            page_num=i,
            context="",
            business_flow="bid_review",
        )
        for i in range(4)
    ]

    started_events = [asyncio.Event() for _ in items]

    async def _fake_full_page(page_b64, vision_client, page_num, context, business_flow):
        idx = page_num
        started_events[idx].set()
        # 等所有 item 都已启动后再返回, 串行实现下永远等不到
        await asyncio.wait_for(
            asyncio.gather(*[ev.wait() for ev in started_events]),
            timeout=2.0,
        )
        return f"content-{idx}"

    with (
        patch("app.core.ai.get_vision_client", return_value=object()),
        patch(
            "app.core.parser.image_analyzer.analyze_full_page",
            new=AsyncMock(side_effect=_fake_full_page),
        ),
    ):
        payload = await asyncio.wait_for(
            run_document_image_vl_batch(
                {"redis": redis},
                project_id=7,
                business_flow="bid_review",
                batch_id="batch-parallel-1",
                items=items,
            ),
            timeout=5.0,
        )

    assert payload["status"] == "completed"
    assert len(payload["items"]) == 4
    assert all(it["success"] for it in payload["items"])
    # 顺序保持: image_key 与入参一致
    assert [it["image_key"] for it in payload["items"]] == [f"page-{i}" for i in range(4)]


@pytest.mark.asyncio
async def test_batch_partial_failure_does_not_kill_others() -> None:
    """单个 item 抛异常必须被局部 catch, 其余 item 正常完成。"""
    redis = _FakeRedis()
    items = [
        full_page_to_vl_item(
            image_key=f"page-{i}",
            page_b64=f"b64-{i}",
            page_num=i,
            context="",
            business_flow="bid_review",
        )
        for i in range(3)
    ]

    async def _flaky_full_page(page_b64, vision_client, page_num, context, business_flow):
        if page_num == 1:
            raise RuntimeError("simulated VL API error")
        return f"content-{page_num}"

    with (
        patch("app.core.ai.get_vision_client", return_value=object()),
        patch(
            "app.core.parser.image_analyzer.analyze_full_page",
            new=AsyncMock(side_effect=_flaky_full_page),
        ),
    ):
        payload = await run_document_image_vl_batch(
            {"redis": redis},
            project_id=7,
            business_flow="bid_review",
            batch_id="batch-fail-1",
            items=items,
        )

    assert payload["status"] == "completed"
    assert len(payload["items"]) == 3
    # 顺序保持
    assert [it["image_key"] for it in payload["items"]] == ["page-0", "page-1", "page-2"]
    # 中间一个失败, 其他成功
    assert payload["items"][0]["success"] is True
    assert payload["items"][1]["success"] is False
    assert "RuntimeError" in payload["items"][1]["error"]
    assert payload["items"][2]["success"] is True


@pytest.mark.asyncio
async def test_batch_result_order_matches_input_for_image_kind() -> None:
    """逐图 (kind=image) 的并发结果顺序必须与 items 入参严格一致。"""
    redis = _FakeRedis()
    images = [_FakeImage(page_num=p) for p in (5, 2, 9, 1)]
    items = [
        image_to_vl_item(img, make_image_key(img, idx), business_flow="bid_review")
        for idx, img in enumerate(images)
    ]

    # 让靠后的 item 先返回, 验证 gather 仍按入参顺序排列结果
    delays = {5: 0.3, 2: 0.05, 9: 0.2, 1: 0.1}

    async def _fake_image_vl(image, _client, route_label="vl", business_flow="bid_review"):
        await asyncio.sleep(delays.get(image.page_num, 0))
        return (f"image-{image.page_num}", route_label)

    with (
        patch("app.core.ai.get_vision_client", return_value=object()),
        patch(
            "app.core.parser.image_analyzer.analyze_image_vl_only",
            new=AsyncMock(side_effect=_fake_image_vl),
        ),
    ):
        payload = await run_document_image_vl_batch(
            {"redis": redis},
            project_id=7,
            business_flow="bid_review",
            batch_id="batch-order-1",
            items=items,
        )

    assert payload["status"] == "completed"
    expected_keys = [it["image_key"] for it in items]
    assert [it["image_key"] for it in payload["items"]] == expected_keys
    assert [it["content"] for it in payload["items"]] == [
        "image-5",
        "image-2",
        "image-9",
        "image-1",
    ]
