from tests.synthetic_data import synthetic_text
"""图片分析路由决策单元测试

覆盖: 路由决策逻辑、OCR 成功不回退、OCR 低置信回退 VL、ocr_engine=None 兼容、
返回路由标记、日志路由统计。
"""

import os
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.parser.image_analyzer import _ocr_priority, analyze_single_image, analyze_images
from app.core.parser.ocr_engine import OcrResult


@dataclass
class FakeImage:
    """测试用图片对象"""
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


class TestOcrPriority:
    def test_large_scan_is_high_priority(self):
        img = FakeImage(width=600, height=800)
        assert _ocr_priority(img) == "high"

    def test_medium_with_cert_keyword_is_medium_priority(self):
        img = FakeImage(
            width=400, height=300,
            spatial_context="本页附有营业执照扫描件",
        )
        assert _ocr_priority(img) == "medium"

    def test_medium_without_keyword_is_low_priority(self):
        img = FakeImage(
            width=400, height=300,
            spatial_context="项目现场照片",
        )
        assert _ocr_priority(img) == "low"

    def test_small_icon_is_low_priority(self):
        img = FakeImage(width=50, height=50)
        assert _ocr_priority(img) == "low"

    def test_medium_no_context_is_low_priority(self):
        img = FakeImage(width=300, height=300, spatial_context="")
        assert _ocr_priority(img) == "low"


@pytest.mark.asyncio
class TestAnalyzeSingleImageRouting:
    async def test_ocr_success_returns_ocr_route(self):
        """OCR 成功时返回 route='ocr' 且不调用 VL"""
        img = FakeImage(width=600, height=800)
        mock_vision = AsyncMock()
        mock_ocr = MagicMock()
        mock_ocr.is_congested = False

        ocr_result = OcrResult(
            raw_text=synthetic_text('营业执照\n', '统一社会信用代码：', '91110000X', 'XXXXXXXXX', '\n名称：测试公司'),
            avg_confidence=0.92,
            is_confident=True,
        )
        mock_ocr.recognize = AsyncMock(return_value=ocr_result)

        with patch(
            "app.core.parser.ocr_postprocessor.postprocess_ocr",
            return_value="这是一份营业执照的扫描件。持证单位: 测试公司",
        ):
            content, route = await analyze_single_image(img, mock_vision, ocr_engine=mock_ocr)

        assert "营业执照" in content
        assert route == "ocr"
        mock_vision.chat_with_images.assert_not_called()

    async def test_ocr_low_confidence_returns_fallback_route(self):
        """OCR 置信度不足时回退 VL 并返回 route='ocr->vl'"""
        img = FakeImage(width=600, height=800)
        mock_vision = AsyncMock()
        mock_vision.chat_with_images = AsyncMock(return_value="VL识别结果：某资质证书")

        mock_ocr = MagicMock()
        mock_ocr.is_congested = False
        ocr_result = OcrResult(
            raw_text="模糊", avg_confidence=0.3, is_confident=False,
        )
        mock_ocr.recognize = AsyncMock(return_value=ocr_result)

        with patch("app.core.parser.image_analyzer._load_image_as_base64", return_value="base64data"):
            content, route = await analyze_single_image(img, mock_vision, ocr_engine=mock_ocr)

        assert "VL识别结果" in content
        assert route == "ocr->vl"
        mock_vision.chat_with_images.assert_called_once()

    async def test_no_ocr_engine_returns_vl_route(self):
        """ocr_engine=None 时返回 route='vl'"""
        img = FakeImage(width=600, height=800)
        mock_vision = AsyncMock()
        mock_vision.chat_with_images = AsyncMock(return_value="VL结果")

        with patch("app.core.parser.image_analyzer._load_image_as_base64", return_value="base64data"):
            content, route = await analyze_single_image(img, mock_vision, ocr_engine=None)

        assert content == "VL结果"
        assert route == "vl"

    async def test_no_file_path_returns_empty(self):
        img = FakeImage(file_path="")
        mock_vision = AsyncMock()
        content, route = await analyze_single_image(img, mock_vision)
        assert content == ""

    async def test_vl_route_skips_ocr(self):
        """路由判定为 VL 时不调用 OCR"""
        img = FakeImage(width=300, height=300, spatial_context="施工现场")
        mock_vision = AsyncMock()
        mock_vision.chat_with_images = AsyncMock(return_value="照片内容")
        mock_ocr = MagicMock()
        mock_ocr.recognize = AsyncMock()

        with patch("app.core.parser.image_analyzer._load_image_as_base64", return_value="base64data"):
            content, route = await analyze_single_image(img, mock_vision, ocr_engine=mock_ocr)

        assert content == "照片内容"
        assert route == "vl"
        mock_ocr.recognize.assert_not_called()

    async def test_ocr_exception_falls_back_to_vl(self):
        img = FakeImage(width=600, height=800)
        mock_vision = AsyncMock()
        mock_vision.chat_with_images = AsyncMock(return_value="VL兜底结果")

        mock_ocr = MagicMock()
        mock_ocr.is_congested = False
        mock_ocr.recognize = AsyncMock(side_effect=RuntimeError("OCR crash"))

        with patch("app.core.parser.image_analyzer._load_image_as_base64", return_value="base64data"):
            content, route = await analyze_single_image(img, mock_vision, ocr_engine=mock_ocr)

        assert content == "VL兜底结果"
        assert route == "ocr->vl"


@pytest.mark.asyncio
class TestOcrEngineExtended:
    async def test_recognize_from_array(self):
        """numpy array 输入应正确解析结果"""
        import numpy as np
        from app.core.parser.ocr_engine import OcrEngine

        engine = OcrEngine(lang="ch", confidence_threshold=0.7, min_text_length=10)
        mock_ocr = MagicMock()
        mock_result = {
            "rec_texts": ["营业执照", synthetic_text('统一社会信用代码:', ' 91110000', 'XXXXXXXXXX')],
            "rec_scores": [0.95, 0.88],
            "dt_polys": [
                np.array([[0, 0], [100, 0], [100, 20], [0, 20]]),
                np.array([[0, 30], [200, 30], [200, 50], [0, 50]]),
            ],
        }
        mock_ocr.predict.return_value = [mock_result]
        engine._ocr = mock_ocr

        fake_img = np.zeros((100, 200, 3), dtype=np.uint8)
        result = engine._recognize_from_array_sync(fake_img)

        assert "营业执照" in result.raw_text
        assert result.is_confident is True
        mock_ocr.predict.assert_called_once()

    async def test_recognize_from_array_empty(self):
        """空 numpy array 应返回空结果"""
        import numpy as np
        from app.core.parser.ocr_engine import OcrEngine

        engine = OcrEngine(lang="ch")
        engine._ocr = MagicMock()

        result = engine._recognize_from_array_sync(np.array([]))
        assert result.raw_text == ""
        assert result.is_confident is False

    async def test_predict_lock_serializes_calls(self):
        """推理锁应阻止并发 predict 调用"""
        import numpy as np
        from app.core.parser.ocr_engine import OcrEngine

        engine = OcrEngine(lang="ch", confidence_threshold=0.5, min_text_length=5)
        mock_ocr = MagicMock()
        mock_result = {
            "rec_texts": ["测试文本内容"],
            "rec_scores": [0.9],
            "dt_polys": [],
        }
        mock_ocr.predict.return_value = [mock_result]
        engine._ocr = mock_ocr

        import threading
        call_times = []
        original_predict = mock_ocr.predict

        def tracking_predict(inp):
            call_times.append(("start", threading.current_thread().ident))
            import time
            time.sleep(0.05)
            call_times.append(("end", threading.current_thread().ident))
            return [mock_result]

        mock_ocr.predict.side_effect = tracking_predict

        results = await asyncio.gather(
            engine.recognize_from_array(np.zeros((10, 10, 3), dtype=np.uint8)),
            engine.recognize_from_array(np.zeros((10, 10, 3), dtype=np.uint8)),
        )

        assert all(r.raw_text for r in results)
        starts = [i for i, (ev, _) in enumerate(call_times) if ev == "start"]
        ends = [i for i, (ev, _) in enumerate(call_times) if ev == "end"]
        assert ends[0] < starts[1], "Second predict should start after first ends (lock serialization)"

    async def test_analyze_images_respects_ocr_internal_backpressure(self):
        """analyze_images 在高并发下应兼容 OCR 引擎内部背压，不出现丢结果"""
        imgs = [
            FakeImage(page_num=1, image_index=0, width=600, height=800),
            FakeImage(page_num=2, image_index=0, width=600, height=800),
            FakeImage(page_num=3, image_index=0, width=600, height=800),
        ]
        mock_vision = AsyncMock()

        class ThrottledOcrEngine:
            def __init__(self) -> None:
                self._sem = asyncio.Semaphore(1)
                self.active = 0
                self.max_active = 0

            @property
            def is_congested(self) -> bool:
                return False

            async def recognize(self, image_path: str, **_kwargs) -> OcrResult:
                async with self._sem:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                    await asyncio.sleep(0.02)
                    self.active -= 1
                    return OcrResult(
                        raw_text=synthetic_text('营业执照\n', '统一社会信用代码:', ' 91110000', 'XXXXXXXXXX'),
                        avg_confidence=0.95,
                        is_confident=True,
                    )

        throttled_ocr = ThrottledOcrEngine()

        with patch(
            "app.core.parser.ocr_postprocessor.postprocess_ocr",
            return_value="这是一份营业执照扫描件。持证单位: 测试公司",
        ):
            await analyze_images(
                images=imgs,
                vision_client=mock_vision,
                concurrency=3,
                ocr_engine=throttled_ocr,
            )

        assert throttled_ocr.max_active == 1
        assert all("营业执照" in img.visual_content for img in imgs)
        mock_vision.chat_with_images.assert_not_called()


# ── 负载感知路由 + 预算控制防御测试 ─────────────────────────────────────


@pytest.mark.asyncio
class TestCongestionDetection:
    async def test_not_congested_when_no_history(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 15000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)
        monkeypatch.setattr(settings, "OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS", 8000)

        from app.core.parser.adaptive_concurrency import OcrAdaptiveConcurrency
        controller = OcrAdaptiveConcurrency()
        assert controller.is_congested is False

    async def test_congested_after_high_queue_wait(self, monkeypatch):
        from app.core.config import settings
        from app.core.parser.adaptive_concurrency import (
            OcrAdaptiveConcurrency, OcrRequestMetric,
        )
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 15000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)
        monkeypatch.setattr(settings, "OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS", 8000)

        controller = OcrAdaptiveConcurrency()
        for _ in range(4):
            await controller.record(OcrRequestMetric(
                latency_ms=500,
                queue_wait_ms=12000,
                success=True,
                is_timeout=False,
                is_transport_error=False,
                is_low_confidence=False,
                weight=1,
            ))

        assert controller.is_congested is True

    async def test_get_stats_includes_congested_flag(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 15000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)
        monkeypatch.setattr(settings, "OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS", 8000)

        from app.core.parser.adaptive_concurrency import OcrAdaptiveConcurrency
        controller = OcrAdaptiveConcurrency()
        stats = controller.get_stats()
        assert "is_congested" in stats


@pytest.mark.asyncio
class TestCongestionBasedRouting:
    async def test_high_priority_still_ocr_when_congested(self):
        """高优先级扫描件在 OCR 拥堵时仍走 OCR"""
        img = FakeImage(width=600, height=800)
        mock_vision = AsyncMock()
        mock_ocr = MagicMock()
        mock_ocr.is_congested = True
        mock_ocr.wait_for_recovery = AsyncMock(return_value=True)

        ocr_result = OcrResult(
            raw_text=synthetic_text('营业执照\n', '统一社会信用代码：', '91110000X', 'XXXXXXXXX', '\n名称：测试公司'),
            avg_confidence=0.92,
            is_confident=True,
        )
        mock_ocr.recognize = AsyncMock(return_value=ocr_result)

        with patch(
            "app.core.parser.ocr_postprocessor.postprocess_ocr",
            return_value="这是一份营业执照的扫描件。持证单位: 测试公司",
        ):
            content, route = await analyze_single_image(img, mock_vision, ocr_engine=mock_ocr)

        assert route == "ocr"
        mock_vision.chat_with_images.assert_not_called()

    async def test_medium_priority_degrades_when_congested(self):
        """中优先级图片在 OCR 拥堵时降级到 VL"""
        img = FakeImage(
            width=400, height=300,
            spatial_context="本页附有营业执照扫描件",
        )
        mock_vision = AsyncMock()
        mock_vision.chat_with_images = AsyncMock(return_value="VL识别结果")
        mock_ocr = MagicMock()
        mock_ocr.is_congested = True
        mock_ocr.recognize = AsyncMock()

        with patch("app.core.parser.image_analyzer._load_image_as_base64", return_value="base64data"):
            content, route = await analyze_single_image(img, mock_vision, ocr_engine=mock_ocr)

        assert route == "ocr_degraded"
        assert content == "VL识别结果"
        mock_ocr.recognize.assert_not_called()

    async def test_medium_priority_uses_ocr_when_not_congested(self):
        """中优先级图片在 OCR 不拥堵时正常走 OCR"""
        img = FakeImage(
            width=400, height=300,
            spatial_context="本页附有营业执照扫描件",
        )
        mock_vision = AsyncMock()
        mock_ocr = MagicMock()
        mock_ocr.is_congested = False

        ocr_result = OcrResult(
            raw_text=synthetic_text('营业执照\n', '统一社会信用代码：', '91110000X', 'XXXXXXXXX', '\n名称：测试公司'),
            avg_confidence=0.92,
            is_confident=True,
        )
        mock_ocr.recognize = AsyncMock(return_value=ocr_result)

        with patch(
            "app.core.parser.ocr_postprocessor.postprocess_ocr",
            return_value="这是一份营业执照。持证单位: 测试公司",
        ):
            content, route = await analyze_single_image(img, mock_vision, ocr_engine=mock_ocr)

        assert route == "ocr"
        mock_ocr.recognize.assert_called_once()


@pytest.mark.asyncio
class TestBudgetControl:
    async def test_budget_limits_medium_priority_ocr(self, monkeypatch):
        """预算耗尽后中优先级图片走 VL 而非 OCR"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "OCR_BUDGET_PER_BATCH", 1)

        imgs = [
            FakeImage(
                page_num=i, image_index=0,
                width=400, height=300,
                spatial_context="营业执照扫描件",
            )
            for i in range(3)
        ]
        mock_vision = AsyncMock()
        mock_vision.chat_with_images = AsyncMock(return_value="VL结果")

        mock_ocr = MagicMock()
        mock_ocr.is_congested = False
        ocr_result = OcrResult(
            raw_text=synthetic_text('营业执照\n', '统一社会信用代码：', '91110000X', 'XXXXXXXXX', '\n名称：测试公司'),
            avg_confidence=0.92,
            is_confident=True,
        )
        mock_ocr.recognize = AsyncMock(return_value=ocr_result)

        with patch(
            "app.core.parser.ocr_postprocessor.postprocess_ocr",
            return_value="这是一份营业执照。持证单位: 测试公司",
        ), patch(
            "app.core.parser.image_analyzer._load_image_as_base64",
            return_value="base64data",
        ):
            await analyze_images(
                images=imgs,
                vision_client=mock_vision,
                concurrency=3,
                ocr_engine=mock_ocr,
            )

        recognized = [img for img in imgs if img.visual_content]
        assert len(recognized) == 3

        ocr_call_count = mock_ocr.recognize.await_count
        assert ocr_call_count <= 1, (
            f"Budget=1 but OCR was called {ocr_call_count} times"
        )

    async def test_high_priority_ignores_budget(self, monkeypatch):
        """高优先级图片不受预算限制"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "OCR_BUDGET_PER_BATCH", 1)

        imgs = [
            FakeImage(page_num=i, image_index=0, width=600, height=800)
            for i in range(3)
        ]
        mock_vision = AsyncMock()
        mock_ocr = MagicMock()
        mock_ocr.is_congested = False
        ocr_result = OcrResult(
            raw_text=synthetic_text('营业执照\n', '统一社会信用代码：', '91110000X', 'XXXXXXXXX', '\n名称：测试公司'),
            avg_confidence=0.92,
            is_confident=True,
        )
        mock_ocr.recognize = AsyncMock(return_value=ocr_result)

        with patch(
            "app.core.parser.ocr_postprocessor.postprocess_ocr",
            return_value="这是一份营业执照。持证单位: 测试公司",
        ):
            await analyze_images(
                images=imgs,
                vision_client=mock_vision,
                concurrency=3,
                ocr_engine=mock_ocr,
            )

        assert mock_ocr.recognize.await_count == 3

    async def test_budget_zero_means_unlimited(self, monkeypatch):
        """预算设为 0 表示不限制"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "OCR_BUDGET_PER_BATCH", 0)

        imgs = [
            FakeImage(
                page_num=i, image_index=0,
                width=400, height=300,
                spatial_context="营业执照扫描件",
            )
            for i in range(5)
        ]
        mock_vision = AsyncMock()
        mock_ocr = MagicMock()
        mock_ocr.is_congested = False
        ocr_result = OcrResult(
            raw_text=synthetic_text('营业执照\n', '统一社会信用代码：', '91110000X', 'XXXXXXXXX', '\n名称：测试公司'),
            avg_confidence=0.92,
            is_confident=True,
        )
        mock_ocr.recognize = AsyncMock(return_value=ocr_result)

        with patch(
            "app.core.parser.ocr_postprocessor.postprocess_ocr",
            return_value="这是一份营业执照。持证单位: 测试公司",
        ):
            await analyze_images(
                images=imgs,
                vision_client=mock_vision,
                concurrency=5,
                ocr_engine=mock_ocr,
            )

        assert mock_ocr.recognize.await_count == 5


@pytest.mark.asyncio
class TestOcrEngineCongested:
    async def test_local_mode_never_congested(self):
        from app.core.parser.ocr_engine import OcrEngine
        engine = OcrEngine(lang="ch", service_url="")
        assert engine.is_congested is False

    async def test_http_mode_no_controller_not_congested(self):
        from app.core.parser.ocr_engine import OcrEngine
        engine = OcrEngine(lang="ch", service_url="http://ocr:9000")
        assert engine.is_congested is False

    async def test_http_mode_congested_delegates_to_controller(self):
        from app.core.parser.ocr_engine import OcrEngine
        engine = OcrEngine(lang="ch", service_url="http://ocr:9000")
        mock_controller = MagicMock()
        mock_controller.is_congested = True
        engine._http_controller = mock_controller
        assert engine.is_congested is True
