"""VL 阶段项目取消语义测试。"""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

# 必须在本文件 patch arq_runtime.raise_if_project_cancelled 之前绑定，
# 否则 vl_stage 首次 import cancellation 会把 fake 冻进模块全局。
import app.core.tasks.cancellation  # noqa: F401
from app.core.arq_runtime import ProjectCancelledError
from app.core.tasks.process.vl_stage import run_vl_stage


def _page_png_b64() -> str:
    image = Image.new("RGB", (32, 32), "white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_scan_heavy_ocr_stops_when_project_cancelled_inside_page_loop(monkeypatch):
    """项目在整页 OCR 内部取消后, 不应继续调用 OCR 引擎。"""

    image = SimpleNamespace(
        page_num=1,
        image_index=0,
        file_path="/tmp/page.png",
        visual_content="",
    )
    page = SimpleNamespace(page_num=1, text="", images=[image])
    parsed_doc = SimpleNamespace(
        pages=[page],
        all_images=[image],
        page_count=1,
        metadata={},
    )
    ocr_calls = 0
    cancel_checks = 0

    def fake_raise_if_cancelled(project_id: int) -> None:
        nonlocal cancel_checks
        cancel_checks += 1
        if cancel_checks >= 2:
            raise ProjectCancelledError(project_id)

    class FakeOcr:
        is_congested = False

        def get_http_stats(self):
            return {"pool_healthy": 1, "pool_total": 1, "mode": "http", "concurrency": 1}

        async def recognize_from_array(self, *_args, **_kwargs):
            nonlocal ocr_calls
            ocr_calls += 1
            return SimpleNamespace(raw_text="SHOULD_NOT_RUN", avg_confidence=1.0, is_confident=True)

    monkeypatch.setattr(
        "app.core.ai_settings.load_ai_settings",
        lambda: SimpleNamespace(
            vision_model_id=1,
            get_config_by_id=lambda *_args: SimpleNamespace(config_type="cloud", model_name="vision"),
        ),
    )
    monkeypatch.setattr("app.core.ai.ai_factory.get_vision_dispatcher", lambda: None)
    monkeypatch.setattr("app.core.tasks.update_heartbeat", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.core.parser.image_recognition_service.get_ocr_engine", lambda: FakeOcr())
    monkeypatch.setattr("app.core.parser.page_renderer.is_scan_heavy_page", lambda *_args: True)
    monkeypatch.setattr(
        "app.core.tasks.document_image_llm_task.probe_document_vl_availability",
        lambda **_kwargs: _async_return((True, "ok")),
    )
    monkeypatch.setattr("app.core.tasks.process_task._compute_scan_concurrency", lambda *_args: 1)
    monkeypatch.setattr(
        "app.core.tasks.process_task._render_pages_sync",
        lambda *_args, **_kwargs: {1: (_page_png_b64(), "")},
    )
    monkeypatch.setattr(
        "app.core.tasks.document_image_llm_task.analyze_full_pages_with_document_llm_subtasks",
        lambda **_kwargs: _async_return({}),
    )
    monkeypatch.setattr(
        "app.core.arq_runtime.raise_if_project_cancelled",
        fake_raise_if_cancelled,
    )

    with pytest.raises(ProjectCancelledError):
        await run_vl_stage(
            parsed_doc=parsed_doc,
            file_path="dummy.pdf",
            doc_project_id=77,
            business_flow="bid_review",
            image_count=1,
            task_name="process_doc_test",
        )

    assert ocr_calls == 0


async def _async_return(value):
    return value
