"""图片识别共享服务单元测试

覆盖:
- get_ocr_engine() 正常/禁用/初始化失败三种场景
- recognize_images() OCR+VL 混合路径 / 纯 VL / 探测失败降级
- recognize_images() 重试行为
"""

import os
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.parser.image_recognition_service import (
    get_ocr_engine,
    get_vl_deps,
    recognize_images,
    RecognitionStats,
)
from app.core.parser.ocr_engine import OcrResult


@dataclass
class FakeImage:
    page_num: int = 1
    image_index: int = 0
    width: int = 600
    height: int = 800
    file_path: str = "/tmp/test.png"
    spatial_context: str = ""
    visual_content: str = ""

    @property
    def size_category(self) -> str:
        area = self.width * self.height
        if area >= 500 * 700:
            return "大幅扫描件"
        elif area >= 200 * 200:
            return "中等图片"
        return "小图标"


class TestGetOcrEngine:
    def test_returns_engine_when_enabled(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_ENABLED", True)

        mock_engine = MagicMock()
        with patch(
            "app.core.parser.ocr_engine.OcrEngine.get_instance",
            return_value=mock_engine,
        ):
            engine = get_ocr_engine()
        assert engine is mock_engine

    def test_returns_none_when_disabled(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_ENABLED", False)
        engine = get_ocr_engine()
        assert engine is None

    def test_returns_none_on_init_failure(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_ENABLED", True)

        with patch(
            "app.core.parser.ocr_engine.OcrEngine.get_instance",
            side_effect=RuntimeError("paddle crash"),
        ):
            engine = get_ocr_engine()
        assert engine is None

    def test_get_vl_deps_passes_project_id_to_vision_client(self):
        mock_client = MagicMock()
        fake_dispatcher = MagicMock(channel_count=2)
        fake_dispatcher.get_primary_group_channel_count.return_value = 3
        from app.core.ai_settings import AISettingsModel, ModelConfigItem

        vision_settings = AISettingsModel(
            vision_model_id="vision-main",
            vision_pool_ids=["vision-main"],
            vision_configs=[
                ModelConfigItem(
                    id="vision-main",
                    api_base="https://api.example.com/v1",
                    api_key="sk-test",
                    model_name="qwen-vl",
                    config_type="cloud",
                )
            ],
        )

        with (
            patch(
                "app.core.ai_settings.load_ai_settings",
                return_value=vision_settings,
            ),
            patch(
                "app.core.ai.ai_factory.get_vision_dispatcher",
                return_value=fake_dispatcher,
            ),
            patch(
                "app.core.ai.get_vision_client",
                return_value=mock_client,
            ) as mock_get_vision_client,
            patch(
                "app.core.parser.adaptive_concurrency.AdaptiveConcurrency",
                return_value=MagicMock(current=3),
            ),
        ):
            deps = get_vl_deps(purpose="writer", project_id=88)

        mock_get_vision_client.assert_called_once_with(project_id=88)
        assert deps.available is True
        assert deps.vision_client is mock_client


@pytest.mark.asyncio
class TestRecognizeImages:
    async def test_ocr_plus_vl_mixed_path(self):
        imgs = [
            FakeImage(page_num=1, width=600, height=800),
            FakeImage(page_num=2, width=600, height=800),
        ]

        mock_ocr = MagicMock()
        mock_ocr.recognize = AsyncMock(
            return_value=OcrResult(
                raw_text="营业执照\n统一社会信用代码: 91110000XX",
                avg_confidence=0.95,
                is_confident=True,
            )
        )

        mock_vision = AsyncMock()
        mock_controller = MagicMock()
        mock_controller.current = 5
        mock_controller.acquire = AsyncMock()
        mock_controller.release = AsyncMock()
        mock_controller.record = AsyncMock()
        mock_controller.reset_window = MagicMock()

        with (
            patch(
                "app.core.parser.image_recognition_service.get_ocr_engine",
                return_value=mock_ocr,
            ),
            patch(
                "app.core.parser.image_recognition_service.get_vl_deps",
                return_value=MagicMock(
                    vision_client=mock_vision,
                    controller=mock_controller,
                    available=True,
                ),
            ),
            patch(
                "app.core.parser.image_recognition_service.probe_vl_availability",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.core.parser.image_recognition_service.analyze_images",
                new_callable=AsyncMock,
                return_value={"ocr_ok": 0},
            ) as mock_analyze,
        ):
            stats = await recognize_images(imgs, purpose="test")

        mock_analyze.assert_called()
        call_kwargs = mock_analyze.call_args
        assert call_kwargs.kwargs.get("ocr_engine") is mock_ocr

    async def test_pure_vl_when_ocr_disabled(self):
        imgs = [FakeImage()]

        mock_vision = AsyncMock()
        mock_controller = MagicMock()
        mock_controller.current = 5
        mock_controller.acquire = AsyncMock()
        mock_controller.release = AsyncMock()
        mock_controller.record = AsyncMock()
        mock_controller.reset_window = MagicMock()

        with (
            patch(
                "app.core.parser.image_recognition_service.get_ocr_engine",
                return_value=None,
            ),
            patch(
                "app.core.parser.image_recognition_service.get_vl_deps",
                return_value=MagicMock(
                    vision_client=mock_vision,
                    controller=mock_controller,
                    available=True,
                ),
            ),
            patch(
                "app.core.parser.image_recognition_service.probe_vl_availability",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.core.parser.image_recognition_service.analyze_images",
                new_callable=AsyncMock,
                return_value={"ocr_ok": 0},
            ) as mock_analyze,
        ):
            stats = await recognize_images(imgs, purpose="test_no_ocr")

        call_kwargs = mock_analyze.call_args
        assert call_kwargs.kwargs.get("ocr_engine") is None

    async def test_probe_failure_skips_recognition(self):
        imgs = [FakeImage()]

        with (
            patch(
                "app.core.parser.image_recognition_service.get_ocr_engine",
                return_value=None,
            ),
            patch(
                "app.core.parser.image_recognition_service.get_vl_deps",
                return_value=MagicMock(
                    vision_client=AsyncMock(),
                    controller=MagicMock(current=5),
                    available=True,
                ),
            ),
            patch(
                "app.core.parser.image_recognition_service.probe_vl_availability",
                new_callable=AsyncMock,
                return_value=(False, "model unavailable"),
            ),
            patch(
                "app.core.parser.image_recognition_service.analyze_images",
                new_callable=AsyncMock,
            ) as mock_analyze,
        ):
            stats = await recognize_images(imgs, purpose="test_probe_fail")

        mock_analyze.assert_not_called()
        assert stats.probe_skipped is True

    async def test_empty_images_returns_immediately(self):
        stats = await recognize_images([], purpose="test_empty")
        assert stats.total == 0

    async def test_retry_is_triggered_for_failed_images(self):
        img = FakeImage()

        call_count = 0

        async def mock_analyze_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                for i in kwargs.get("images", []):
                    i.visual_content = "recognized"

        mock_controller = MagicMock()
        mock_controller.current = 5
        mock_controller.acquire = AsyncMock()
        mock_controller.release = AsyncMock()
        mock_controller.record = AsyncMock()
        mock_controller.reset_window = MagicMock()

        with (
            patch(
                "app.core.parser.image_recognition_service.get_ocr_engine",
                return_value=None,
            ),
            patch(
                "app.core.parser.image_recognition_service.get_vl_deps",
                return_value=MagicMock(
                    vision_client=AsyncMock(),
                    controller=mock_controller,
                    available=True,
                ),
            ),
            patch(
                "app.core.parser.image_recognition_service.probe_vl_availability",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.core.parser.image_recognition_service.analyze_images",
                new_callable=AsyncMock,
                side_effect=mock_analyze_side_effect,
            ) as mock_analyze,
        ):
            stats = await recognize_images([img], purpose="test_retry")

        assert mock_analyze.call_count >= 2
        assert stats.recognized == 1

    async def test_offload_confident_ocr_does_not_enqueue_vl(self):
        img = FakeImage(width=600, height=800)
        mock_ocr = MagicMock()
        mock_ocr.is_congested = False
        mock_ocr.recognize = AsyncMock(
            return_value=OcrResult(
                raw_text="营业执照\n统一社会信用代码: 91110000XX",
                avg_confidence=0.95,
                is_confident=True,
            )
        )

        with (
            patch(
                "app.core.parser.image_recognition_service.get_ocr_engine",
                return_value=mock_ocr,
            ),
            patch(
                "app.core.tasks.writing_image_llm_task.enqueue_image_llm_job",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            stats = await recognize_images(
                [img],
                purpose="offload_ocr",
                project_id=42,
                offload_llm=True,
            )

        mock_enqueue.assert_not_called()
        assert stats.ocr_ok == 1
        assert stats.recognized == 1
        assert img.visual_content

    async def test_offload_vl_subtask_applies_result(self):
        img = FakeImage(width=120, height=120)

        async def fake_wait(_batch_id, **_kwargs):
            return {
                "status": "completed",
                "items": [
                    {
                        "image_key": f"{img.file_path}|{img.page_num}|{img.image_index}",
                        "content": "这是一张设备铭牌",
                        "success": True,
                    }
                ],
            }

        with (
            patch(
                "app.core.parser.image_recognition_service.get_ocr_engine",
                return_value=None,
            ),
            patch(
                "app.core.tasks.writing_image_llm_task.enqueue_image_llm_job",
                new_callable=AsyncMock,
                return_value=(True, "queued"),
            ) as mock_enqueue,
            patch(
                "app.core.tasks.writing_image_llm_task.wait_for_image_llm_result",
                new_callable=AsyncMock,
                side_effect=fake_wait,
            ),
        ):
            stats = await recognize_images(
                [img],
                purpose="offload_vl",
                project_id=42,
                max_retry_rounds=0,
                offload_llm=True,
            )

        mock_enqueue.assert_awaited_once()
        assert stats.queued_vl == 1
        assert stats.vl_ok == 1
        assert stats.recognized == 1
        assert img.visual_content == "这是一张设备铭牌"
