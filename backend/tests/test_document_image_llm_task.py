"""通用文档图片 LLM 子任务合同测试。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.tasks.document_image_llm_task import (
    DOCUMENT_IMAGE_VL_JOB,
    FullPageVlRequest,
    analyze_full_pages_with_document_llm_subtasks,
    full_page_to_vl_item,
    image_to_vl_item,
    make_image_key,
    recognize_images_with_document_llm_subtasks,
    run_document_image_vl_batch,
)
from app.core.arq_runtime import ProjectCancelledError


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
    def __init__(self) -> None:
        self.payloads: dict[str, str] = {}

    async def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.payloads[key] = value
        return True


@pytest.mark.asyncio
async def test_run_document_image_vl_batch_handles_image_and_full_page() -> None:
    """同一 LLM 子任务批次必须能处理逐图和整页两类 payload。"""

    redis = _FakeRedis()
    img = _FakeImage()
    items = [
        image_to_vl_item(
            img,
            make_image_key(img),
            business_flow="collusion",
            route_hint="vl",
        ),
        full_page_to_vl_item(
            image_key="full-page-1",
            page_b64="base64",
            page_num=2,
            context="ctx",
            business_flow="collusion",
        ),
    ]

    async def fake_image_vl(_image, _client, route_label="vl", business_flow="bid_review"):
        return (f"{business_flow}:{route_label}:image", route_label)

    async def fake_full_page(
        page_b64,
        vision_client,
        page_num,
        context="",
        business_flow="bid_review",
    ):
        assert vision_client is not None
        return f"{business_flow}:{page_num}:{page_b64}:{context}"

    with (
        patch("app.core.ai.get_vision_client", return_value=object()),
        patch(
            "app.core.parser.image_analyzer.analyze_image_vl_only",
            new=AsyncMock(side_effect=fake_image_vl),
        ),
        patch(
            "app.core.parser.image_analyzer.analyze_full_page",
            new=AsyncMock(side_effect=fake_full_page),
        ),
    ):
        payload = await run_document_image_vl_batch(
            {"redis": redis},
            project_id=7,
            business_flow="collusion",
            batch_id="batch-1",
            items=items,
        )

    assert payload["status"] == "completed"
    assert [item["success"] for item in payload["items"]] == [True, True]
    assert payload["items"][0]["content"] == "collusion:vl:image"
    assert payload["items"][1]["content"] == "collusion:2:base64:ctx"
    assert redis.payloads


@pytest.mark.asyncio
async def test_recognize_images_offloads_vl_to_document_job() -> None:
    """OCR 无结果时，逐图 VL 必须投递 document_image_vl_job。"""

    img = _FakeImage()

    async def fake_wait(_batch_id, **_kwargs):
        # **_kwargs 兼容 新增的 keepalive_callback / keepalive_interval
        return {
            "status": "completed",
            "items": [
                {
                    "image_key": make_image_key(img, 0),
                    "content": "识别结果",
                    "success": True,
                }
            ],
        }

    with (
        patch(
            "app.core.tasks.document_image_llm_task.enqueue_document_image_llm_job",
            new_callable=AsyncMock,
            return_value=(True, "queued"),
        ) as mock_enqueue,
        patch(
            "app.core.tasks.document_image_llm_task.wait_for_document_image_llm_result",
            new_callable=AsyncMock,
            side_effect=fake_wait,
        ),
    ):
        stats = await recognize_images_with_document_llm_subtasks(
            [img],
            project_id=7,
            business_flow="bid_review",
            ocr_engine=None,
            max_retry_rounds=0,
        )

    mock_enqueue.assert_awaited_once()
    kwargs = mock_enqueue.await_args.kwargs
    assert kwargs["business_flow"] == "bid_review"
    assert kwargs["items"][0]["kind"] == "image"
    assert kwargs["items"][0]["business_flow"] == "bid_review"
    assert kwargs["items"][0]["route_hint"] == "vl"
    assert kwargs["items"][0]
    assert DOCUMENT_IMAGE_VL_JOB == "document_image_vl_job"
    assert stats.queued_vl == 1
    assert stats.vl_ok == 1
    assert img.visual_content == "识别结果"


@pytest.mark.asyncio
async def test_full_pages_are_offloaded_as_one_batch_job() -> None:
    """多页整页 VL 应合并为一个 LLM 子任务批次，避免单页 job 洪峰。"""

    requests = [
        FullPageVlRequest(
            page_b64=f"b64-{page_num}",
            page_num=page_num,
            context=f"ctx-{page_num}",
        )
        for page_num in range(1, 4)
    ]

    async def fake_wait(_batch_id, **_kwargs):
        return {
            "status": "completed",
            "items": [
                {
                    "image_key": f"full_page|{page_num}",
                    "content": f"content-{page_num}",
                    "success": True,
                }
                for page_num in range(1, 4)
            ],
        }

    with (
        patch(
            "app.core.tasks.document_image_llm_task.enqueue_document_image_llm_job",
            new_callable=AsyncMock,
            return_value=(True, "queued"),
        ) as mock_enqueue,
        patch(
            "app.core.tasks.document_image_llm_task.wait_for_document_image_llm_result",
            new_callable=AsyncMock,
            side_effect=fake_wait,
        ),
    ):
        result = await analyze_full_pages_with_document_llm_subtasks(
            project_id=7,
            business_flow="bid_review",
            pages=requests,
            batch_size=10,
        )

    mock_enqueue.assert_awaited_once()
    kwargs = mock_enqueue.await_args.kwargs
    assert kwargs["business_flow"] == "bid_review"
    assert len(kwargs["items"]) == 3
    assert [item["kind"] for item in kwargs["items"]] == ["full_page"] * 3
    assert [item["page_num"] for item in kwargs["items"]] == [1, 2, 3]
    assert [item["context"] for item in kwargs["items"]] == ["ctx-1", "ctx-2", "ctx-3"]
    assert result == {1: "content-1", 2: "content-2", 3: "content-3"}


@pytest.mark.asyncio
async def test_full_page_batches_wait_before_enqueueing_next_batch() -> None:
    """整页 VL 超过批大小时必须批次间等待，形成 ProcessWorker 侧背压。"""

    requests = [
        FullPageVlRequest(page_b64=f"b64-{page_num}", page_num=page_num)
        for page_num in range(1, 6)
    ]
    call_order: list[str] = []

    async def fake_enqueue(**kwargs):
        batch_index = len([entry for entry in call_order if entry.startswith("enqueue")]) + 1
        call_order.append(f"enqueue-{batch_index}:{len(kwargs['items'])}")
        return (True, "queued")

    async def fake_wait(_batch_id, **kwargs):
        batch_index = len([entry for entry in call_order if entry.startswith("wait")]) + 1
        call_order.append(f"wait-{batch_index}")
        assert kwargs["keepalive_callback"] is keepalive
        if batch_index == 1:
            pages = (1, 2)
        elif batch_index == 2:
            pages = (3, 4)
        else:
            pages = (5,)
        return {
            "status": "completed",
            "items": [
                {
                    "image_key": f"full_page|{page_num}",
                    "content": f"content-{page_num}",
                    "success": True,
                }
                for page_num in pages
            ],
        }

    def keepalive() -> None:
        return None

    with (
        patch(
            "app.core.tasks.document_image_llm_task.enqueue_document_image_llm_job",
            new_callable=AsyncMock,
            side_effect=fake_enqueue,
        ) as mock_enqueue,
        patch(
            "app.core.tasks.document_image_llm_task.wait_for_document_image_llm_result",
            new_callable=AsyncMock,
            side_effect=fake_wait,
        ),
    ):
        result = await analyze_full_pages_with_document_llm_subtasks(
            project_id=7,
            business_flow="bid_review",
            pages=requests,
            batch_size=2,
            keepalive_callback=keepalive,
        )

    assert mock_enqueue.await_count == 3
    assert call_order == [
        "enqueue-1:2",
        "wait-1",
        "enqueue-2:2",
        "wait-2",
        "enqueue-3:1",
        "wait-3",
    ]
    assert result == {
        1: "content-1",
        2: "content-2",
        3: "content-3",
        4: "content-4",
        5: "content-5",
    }


@pytest.mark.asyncio
async def test_full_page_batches_report_progress_after_each_batch() -> None:
    """ 防御: 整页 VL 每个批次返回后必须立即上报页级进度。"""

    requests = [
        FullPageVlRequest(page_b64=f"b64-{page_num}", page_num=page_num)
        for page_num in range(1, 5)
    ]
    call_order: list[str] = []
    callbacks: list[tuple[int, str]] = []

    async def fake_enqueue(**kwargs):
        call_order.append(f"enqueue:{len(kwargs['items'])}")
        return (True, "queued")

    async def fake_wait(_batch_id, **_kwargs):
        wait_index = len([entry for entry in call_order if entry.startswith("wait")]) + 1
        call_order.append(f"wait:{wait_index}")
        pages = (1, 2) if wait_index == 1 else (3, 4)
        return {
            "status": "completed",
            "items": [
                {
                    "image_key": f"full_page|{page_num}",
                    "content": f"content-{page_num}",
                    "success": True,
                }
                for page_num in pages
            ],
        }

    def on_page_progress(page_num: int, content: str) -> None:
        callbacks.append((page_num, content))
        call_order.append(f"callback:{page_num}")

    with (
        patch(
            "app.core.tasks.document_image_llm_task.enqueue_document_image_llm_job",
            new_callable=AsyncMock,
            side_effect=fake_enqueue,
        ),
        patch(
            "app.core.tasks.document_image_llm_task.wait_for_document_image_llm_result",
            new_callable=AsyncMock,
            side_effect=fake_wait,
        ),
    ):
        result = await analyze_full_pages_with_document_llm_subtasks(
            project_id=7,
            business_flow="bid_review",
            pages=requests,
            batch_size=2,
            progress_callback=on_page_progress,
        )

    assert call_order == [
        "enqueue:2",
        "wait:1",
        "callback:1",
        "callback:2",
        "enqueue:2",
        "wait:2",
        "callback:3",
        "callback:4",
    ]
    assert callbacks == [
        (1, "content-1"),
        (2, "content-2"),
        (3, "content-3"),
        (4, "content-4"),
    ]
    assert result == {
        1: "content-1",
        2: "content-2",
        3: "content-3",
        4: "content-4",
    }


@pytest.mark.asyncio
async def test_full_page_batches_stop_before_enqueue_when_project_cancelled() -> None:
    """: 项目取消后父任务不得继续投递下一批整页 VL 子任务。"""

    requests = [
        FullPageVlRequest(page_b64=f"b64-{page_num}", page_num=page_num)
        for page_num in range(1, 3)
    ]

    with (
        patch(
            "app.core.tasks.document_image_llm_task.is_project_cancelled",
            return_value=True,
        ),
        patch(
            "app.core.tasks.document_image_llm_task.enqueue_document_image_llm_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        with pytest.raises(ProjectCancelledError):
            await analyze_full_pages_with_document_llm_subtasks(
                project_id=7,
                business_flow="bid_review",
                pages=requests,
                batch_size=2,
            )

    mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_recognize_images_stops_before_vl_enqueue_when_project_cancelled() -> None:
    """: 逐图 VL 重试循环入队前必须响应项目取消墓碑。"""

    img = _FakeImage()

    async def fake_ocr_first(_img, _ocr_engine):
        return "", "ocr->vl", True

    with (
        patch(
            "app.core.tasks.document_image_llm_task.is_project_cancelled",
            return_value=True,
        ),
        patch(
            "app.core.parser.image_analyzer.analyze_image_ocr_first",
            new=AsyncMock(side_effect=fake_ocr_first),
        ),
        patch(
            "app.core.tasks.document_image_llm_task.enqueue_document_image_llm_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        with pytest.raises(ProjectCancelledError):
            await recognize_images_with_document_llm_subtasks(
                [img],
                project_id=7,
                business_flow="bid_review",
                ocr_engine=None,
                max_retry_rounds=0,
            )

    mock_enqueue.assert_not_awaited()
