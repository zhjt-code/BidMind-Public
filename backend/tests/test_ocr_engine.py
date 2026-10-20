from tests.synthetic_data import synthetic_text
"""OcrEngine 单元测试

覆盖: 本地模式(初始化/单图/批量/异常) + HTTP sidecar 模式(成功/超时/错误)。
PaddleOCR 实际推理和 HTTP 调用通过 Mock 隔离。
"""

import asyncio
import os
import pytest
from contextlib import asynccontextmanager
from unittest.mock import patch, MagicMock, AsyncMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.parser.adaptive_concurrency import (
    OcrAdaptiveConcurrency,
    OcrRequestMetric,
)
from app.core.parser.ocr_engine import OcrEngine, OcrResult, OcrTextRegion


@pytest.fixture(autouse=True)
def reset_singleton():
    """每个测试前重置单例"""
    OcrEngine.reset_instance()
    yield
    OcrEngine.reset_instance()


class TestOcrResult:
    def test_empty_result(self):
        r = OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)
        assert r.raw_text == ""
        assert r.avg_confidence == 0.0
        assert r.is_confident is False
        assert r.regions == []

    def test_confident_result(self):
        r = OcrResult(
            raw_text=synthetic_text('营业执照\n', '统一社会信用代码:', ' 91110000', 'XXXXXXXXXX'),
            avg_confidence=0.92,
            regions=[
                OcrTextRegion(text="营业执照", confidence=0.95),
                OcrTextRegion(text=synthetic_text('统一社会信用代码:', ' 91110000', 'XXXXXXXXXX'), confidence=0.89),
            ],
            is_confident=True,
        )
        assert r.is_confident is True
        assert len(r.regions) == 2


class TestOcrEngine:
    def test_singleton(self):
        e1 = OcrEngine.get_instance(lang="ch")
        e2 = OcrEngine.get_instance()
        assert e1 is e2

    def test_init_failure_returns_empty(self):
        engine = OcrEngine(lang="ch")
        with patch("app.core.parser.ocr_engine.OcrEngine._ensure_initialized", return_value=False):
            result = engine._recognize_sync("nonexistent.png")
        assert result.raw_text == ""
        assert result.is_confident is False

    def test_nonexistent_file(self):
        engine = OcrEngine(lang="ch")
        engine._ocr = MagicMock()
        result = engine._recognize_sync("/path/that/does/not/exist.png")
        assert result.raw_text == ""

    @patch("app.core.parser.ocr_engine.OcrEngine._ensure_initialized", return_value=True)
    def test_recognize_success(self, mock_init):
        """PaddleOCR 3.x predict() 返回 dict-like OCRResult"""
        import numpy as np
        engine = OcrEngine(
            lang="ch",
            confidence_threshold=0.7,
            min_text_length=10,
        )
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

        with patch("pathlib.Path.exists", return_value=True):
            result = engine._recognize_sync("test.png")

        assert "营业执照" in result.raw_text
        assert result.avg_confidence > 0.8
        assert result.is_confident is True
        assert len(result.regions) == 2

    @patch("app.core.parser.ocr_engine.OcrEngine._ensure_initialized", return_value=True)
    def test_recognize_low_confidence(self, mock_init):
        engine = OcrEngine(
            lang="ch",
            confidence_threshold=0.9,
            min_text_length=10,
        )
        mock_ocr = MagicMock()
        mock_result = {
            "rec_texts": ["模糊文字"],
            "rec_scores": [0.3],
            "dt_polys": [],
        }
        mock_ocr.predict.return_value = [mock_result]
        engine._ocr = mock_ocr

        with patch("pathlib.Path.exists", return_value=True):
            result = engine._recognize_sync("blurry.png")

        assert result.is_confident is False

    @patch("app.core.parser.ocr_engine.OcrEngine._ensure_initialized", return_value=True)
    def test_recognize_empty_result(self, mock_init):
        engine = OcrEngine(lang="ch")
        mock_ocr = MagicMock()
        mock_ocr.predict.return_value = []
        engine._ocr = mock_ocr

        with patch("pathlib.Path.exists", return_value=True):
            result = engine._recognize_sync("empty.png")

        assert result.raw_text == ""
        assert result.is_confident is False

    @patch("app.core.parser.ocr_engine.OcrEngine._ensure_initialized", return_value=True)
    def test_recognize_empty_rec_texts(self, mock_init):
        engine = OcrEngine(lang="ch")
        mock_ocr = MagicMock()
        mock_result = {"rec_texts": [], "rec_scores": [], "dt_polys": []}
        mock_ocr.predict.return_value = [mock_result]
        engine._ocr = mock_ocr

        with patch("pathlib.Path.exists", return_value=True):
            result = engine._recognize_sync("empty2.png")

        assert result.raw_text == ""
        assert result.is_confident is False

    @patch("app.core.parser.ocr_engine.OcrEngine._ensure_initialized", return_value=True)
    def test_recognize_exception_handling(self, mock_init):
        engine = OcrEngine(lang="ch")
        mock_ocr = MagicMock()
        mock_ocr.predict.side_effect = RuntimeError("out of memory")
        engine._ocr = mock_ocr

        with patch("pathlib.Path.exists", return_value=True):
            result = engine._recognize_sync("crash.png")

        assert result.raw_text == ""
        assert result.is_confident is False


@pytest.mark.asyncio
class TestOcrEngineAsync:
    async def test_recognize_async(self):
        engine = OcrEngine(lang="ch")
        expected = OcrResult(raw_text="测试文本内容足够长度", avg_confidence=0.9, is_confident=True)
        with patch.object(engine, "_recognize_sync", return_value=expected):
            result = await engine.recognize("test.png")
        assert result.raw_text == "测试文本内容足够长度"
        assert result.is_confident is True

    async def test_recognize_batch(self):
        engine = OcrEngine(lang="ch")
        r1 = OcrResult(raw_text="文件一", avg_confidence=0.9, is_confident=True)
        r2 = OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

        call_count = 0

        def _mock_sync(path):
            nonlocal call_count
            call_count += 1
            return r1 if call_count == 1 else r2

        with patch.object(engine, "_recognize_sync", side_effect=_mock_sync):
            results = await engine.recognize_batch(["a.png", "b.png"])

        assert len(results) == 2
        assert results[0].is_confident is True
        assert results[1].is_confident is False


class TestOcrEngineMode:
    def test_local_mode_when_no_url(self):
        engine = OcrEngine(lang="ch", service_url="")
        assert engine._mode == "local"

    def test_http_mode_when_url_set(self):
        engine = OcrEngine(lang="ch", service_url="http://ocr:9000")
        assert engine._mode == "http"

    def test_http_mode_strips_trailing_slash(self):
        engine = OcrEngine(lang="ch", service_url="http://ocr:9000/")
        assert engine._service_url == "http://ocr:9000"


@pytest.mark.asyncio
class TestOcrEngineHttpMode:
    async def test_http_predict_success(self):
        engine = OcrEngine(
            lang="ch",
            confidence_threshold=0.7,
            min_text_length=10,
            service_url="http://ocr:9000",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "raw_text": synthetic_text('营业执照\n', '统一社会信用代码:', ' 91110000', 'XXXXXXXXXX'),
            "avg_confidence": 0.92,
            "texts": ["营业执照", synthetic_text('统一社会信用代码:', ' 91110000', 'XXXXXXXXXX')],
            "scores": [0.95, 0.88],
            "is_confident": True,
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        engine._pool_clients[0] = mock_client

        result = await engine._http_predict("fake_b64_data")

        assert "营业执照" in result.raw_text
        assert result.avg_confidence == 0.92
        assert result.is_confident is True
        assert len(result.regions) == 2
        mock_client.post.assert_called_once()

    async def test_http_predict_timeout(self):
        import httpx
        engine = OcrEngine(
            lang="ch",
            service_url="http://ocr:9000",
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        engine._pool_clients[0] = mock_client

        result = await engine._http_predict("fake_b64")

        assert result.raw_text == ""
        assert result.is_confident is False

    async def test_http_predict_server_error(self):
        import httpx
        engine = OcrEngine(
            lang="ch",
            service_url="http://ocr:9000",
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        engine._pool_clients[0] = mock_client

        result = await engine._http_predict("fake_b64")

        assert result.raw_text == ""
        assert result.is_confident is False
        assert engine._pool_healthy[0] is False

    async def test_http_predict_respects_concurrency_limit(self):
        engine = OcrEngine(
            lang="ch",
            service_url="http://ocr:9000",
            http_max_concurrency=1,
            queue_warn_ms=0,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "raw_text": "测试文本",
            "avg_confidence": 0.95,
            "texts": ["测试文本"],
            "scores": [0.95],
            "is_confident": True,
        }

        active = 0
        max_active = 0

        async def delayed_post(*args, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1
            return mock_response

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=delayed_post)
        engine._pool_clients[0] = mock_client

        results = await asyncio.gather(
            engine._http_predict("fake_b64_1"),
            engine._http_predict("fake_b64_2"),
            engine._http_predict("fake_b64_3"),
        )

        assert max_active == 1
        assert all(r.is_confident for r in results)
        assert mock_client.post.await_count == 3

    async def test_http_predict_connect_error(self):
        import httpx
        engine = OcrEngine(
            lang="ch",
            service_url="http://ocr:9000",
        )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError(
            "connect failed", request=MagicMock()
        ))
        engine._pool_clients[0] = mock_client

        result = await engine._http_predict("fake_b64")

        assert result.raw_text == ""
        assert result.is_confident is False

    async def test_recognize_file_via_http(self):
        """recognize() 在 HTTP 模式下读文件 -> base64 -> 发 HTTP"""
        import tempfile
        engine = OcrEngine(lang="ch", service_url="http://ocr:9000")

        expected = OcrResult(
            raw_text="测试", avg_confidence=0.9,
            regions=[], is_confident=True,
        )
        engine._http_predict = AsyncMock(return_value=expected)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            tmp_path = f.name

        try:
            result = await engine.recognize(tmp_path)
            assert result.is_confident is True
            engine._http_predict.assert_called_once()
        finally:
            os.unlink(tmp_path)

    async def test_recognize_array_via_http(self):
        """recognize_from_array() 在 HTTP 模式下 numpy -> base64 -> 发 HTTP"""
        import numpy as np
        engine = OcrEngine(lang="ch", service_url="http://ocr:9000")

        expected = OcrResult(
            raw_text="测试数组", avg_confidence=0.85,
            regions=[], is_confident=True,
        )
        engine._http_predict = AsyncMock(return_value=expected)

        fake_img = np.zeros((100, 200, 3), dtype=np.uint8)
        result = await engine.recognize_from_array(fake_img)

        assert result.is_confident is True
        engine._http_predict.assert_called_once()

    async def test_recognize_array_forwards_request_class(self):
        """整页 OCR 应用类别标记而非权重扭曲容量单位。"""
        import numpy as np
        engine = OcrEngine(lang="ch", service_url="http://ocr:9000")
        engine._http_predict = AsyncMock(return_value=OcrResult(
            raw_text="整页文本",
            avg_confidence=0.9,
            regions=[],
            is_confident=True,
        ))

        fake_img = np.zeros((100, 200, 3), dtype=np.uint8)
        await engine.recognize_from_array(
            fake_img,
            request_class="full_page",
        )

        engine._http_predict.assert_called_once()
        assert (
            engine._http_predict.call_args.kwargs["request_class"]
            == "full_page"
        )

    async def test_recognize_array_forwards_flow_id(self):
        """OCR 公共接口必须把业务流程标识传给底层公平调度器。"""
        import numpy as np
        engine = OcrEngine(lang="ch", service_url="http://ocr:9000")
        engine._http_predict = AsyncMock(return_value=OcrResult(
            raw_text="整页文本",
            avg_confidence=0.9,
            regions=[],
            is_confident=True,
        ))

        fake_img = np.zeros((100, 200, 3), dtype=np.uint8)
        await engine.recognize_from_array(
            fake_img,
            request_class="full_page",
            flow_id="project:7",
        )

        assert engine._http_predict.call_args.kwargs["flow_id"] == "project:7"

    async def test_http_predict_enters_flow_scheduler_with_flow_id(self):
        """HTTP OCR 调用前必须先通过流程级公平限流器。"""
        engine = OcrEngine(
            lang="ch",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )

        class _FakeScheduler:
            def __init__(self):
                self.calls = []

            @asynccontextmanager
            async def slot(self, flow_id, request_class, *, capacity):
                self.calls.append((flow_id, request_class, capacity))
                yield

        fake_scheduler = _FakeScheduler()
        engine._flow_scheduler = fake_scheduler

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "raw_text": "OK",
            "avg_confidence": 0.9,
            "texts": ["OK"],
            "scores": [0.9],
            "is_confident": True,
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        engine._pool_clients[0] = mock_client
        engine._pool_clients[1] = mock_client

        await engine._http_predict(
            "fake_b64",
            request_class="full_page",
            flow_id="project:7",
        )

        assert fake_scheduler.calls == [("project:7", "full_page", 2)]


@pytest.mark.asyncio
class TestOcrAdaptiveConcurrency:
    async def test_increases_when_window_is_stable(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 10000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)

        controller = OcrAdaptiveConcurrency()
        for _ in range(4):
            await controller.record(OcrRequestMetric(
                latency_ms=800,
                queue_wait_ms=1200,
                success=True,
                is_timeout=False,
                is_transport_error=False,
                is_low_confidence=False,
                weight=1,
            ))

        assert controller.current == 2

    async def test_reduces_when_timeout_occurs(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 2)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 10000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)

        controller = OcrAdaptiveConcurrency()
        metrics = [
            OcrRequestMetric(900, 1000, True, False, False, False, 1),
            OcrRequestMetric(950, 1100, True, False, False, False, 1),
            OcrRequestMetric(120000, 3000, False, True, False, False, 1),
            OcrRequestMetric(1000, 1200, True, False, False, False, 1),
        ]
        for metric in metrics:
            await controller.record(metric)

        assert controller.current == 1

    async def test_queue_wait_worsening_blocks_further_increase(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 10000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)

        controller = OcrAdaptiveConcurrency()
        for _ in range(4):
            await controller.record(OcrRequestMetric(
                latency_ms=700,
                queue_wait_ms=1000,
                success=True,
                is_timeout=False,
                is_transport_error=False,
                is_low_confidence=False,
                weight=1,
            ))

        assert controller.current == 2

    async def test_queue_wait_worsening_allows_growth_below_two_per_instance(self, monkeypatch):
        """低于实例数两倍时, 排队上升是容量不足信号, 不应阻止增长。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 10000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)

        controller = OcrAdaptiveConcurrency(instance_count=5)
        controller._increase_cooldown = 0
        for _ in range(4):
            await controller.record(OcrRequestMetric(
                latency_ms=700,
                queue_wait_ms=1000,
                success=True,
                is_timeout=False,
                is_transport_error=False,
                is_low_confidence=False,
                weight=1,
                request_class="per_image",
            ))

        assert controller.current == 6

        for _ in range(4):
            await controller.record(OcrRequestMetric(
                latency_ms=750,
                queue_wait_ms=15000,
                success=True,
                is_timeout=False,
                is_transport_error=False,
                is_low_confidence=False,
                weight=1,
                request_class="per_image",
            ))

        assert 6 < controller.current <= 10

    async def test_mixed_request_classes_do_not_trigger_tail_latency(self, monkeypatch):
        """整页与逐图混流不应用全局 p95/p50 误判尾延迟恶化。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 8)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 10000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)

        controller = OcrAdaptiveConcurrency(instance_count=1)
        metrics = [
            OcrRequestMetric(200, 0, True, False, False, False, 1, request_class="per_image"),
            OcrRequestMetric(220, 0, True, False, False, False, 1, request_class="per_image"),
            OcrRequestMetric(240, 0, True, False, False, False, 1, request_class="per_image"),
            OcrRequestMetric(260, 0, True, False, False, False, 1, request_class="per_image"),
            OcrRequestMetric(2000, 0, True, False, False, False, 1, request_class="full_page"),
            OcrRequestMetric(2100, 0, True, False, False, False, 1, request_class="full_page"),
            OcrRequestMetric(2200, 0, True, False, False, False, 1, request_class="full_page"),
            OcrRequestMetric(2300, 0, True, False, False, False, 1, request_class="full_page"),
        ]
        for metric in metrics:
            await controller.record(metric)

        assert controller.current == 2

        for _ in range(4):
            await controller.record(OcrRequestMetric(
                latency_ms=750,
                queue_wait_ms=15000,
                success=True,
                is_timeout=False,
                is_transport_error=False,
                is_low_confidence=False,
                weight=1,
            ))

        assert controller.current == 2

    async def test_weight_is_clamped_to_current_capacity(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 10000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)

        controller = OcrAdaptiveConcurrency()
        async with controller.weighted(weight=2):
            assert controller.current == 1

    async def test_instance_count_scales_max_concurrency(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 3)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 10000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 8000)

        c1 = OcrAdaptiveConcurrency(instance_count=1)
        c2 = OcrAdaptiveConcurrency(instance_count=2)
        c3 = OcrAdaptiveConcurrency(instance_count=3)

        assert c1._max_c == 3
        assert c2._max_c == 6
        assert c3._max_c == 9


@pytest.mark.asyncio
class TestOcrEnginePool:
    async def test_single_url_backward_compatible(self):
        engine = OcrEngine(lang="ch", service_url="http://ocr:9000")
        assert engine._mode == "http"
        assert len(engine._service_urls) == 1
        assert engine._service_urls[0] == "http://ocr:9000"

    async def test_multi_urls_override_single(self):
        engine = OcrEngine(
            lang="ch",
            service_url="http://ocr:9000",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )
        assert engine._mode == "http"
        assert len(engine._service_urls) == 2
        assert engine._service_urls[0] == "http://ocr-1:9000"

    async def test_least_inflight_selection_prefers_idle_instance(self):
        engine = OcrEngine(
            lang="ch",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )
        engine._pool_inflight[0] = 3
        engine._pool_inflight[1] = 0

        assert engine._select_pool_index() == 1

    async def test_least_inflight_selection_round_robins_ties(self):
        engine = OcrEngine(
            lang="ch",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )

        idx1 = engine._select_pool_index()
        idx2 = engine._select_pool_index()
        assert idx1 != idx2

    async def test_unhealthy_instance_skipped(self):
        engine = OcrEngine(
            lang="ch",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )
        engine._pool_healthy[0] = False
        for _ in range(3):
            idx = engine._select_pool_index()
            assert idx == 1

    async def test_all_unhealthy_returns_negative(self):
        engine = OcrEngine(
            lang="ch",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )
        engine._pool_healthy[0] = False
        engine._pool_healthy[1] = False
        idx = engine._select_pool_index()
        assert idx == -1

    async def test_pool_stats_include_pool_info(self):
        engine = OcrEngine(
            lang="ch",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )
        stats = engine.get_http_stats()
        assert stats["pool_total"] == 2
        assert stats["pool_healthy"] == 2
        assert stats["pool_inflight"] == [0, 0]

        engine._pool_healthy[0] = False
        stats = engine.get_http_stats()
        assert stats["pool_healthy"] == 1

    async def test_connect_error_marks_instance_unhealthy(self):
        import httpx
        engine = OcrEngine(
            lang="ch",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )

        mock_client_bad = MagicMock()
        mock_client_bad.post = AsyncMock(
            side_effect=httpx.ConnectError("connect failed", request=MagicMock())
        )
        engine._pool_clients[0] = mock_client_bad

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "raw_text": "OK", "avg_confidence": 0.9,
            "texts": ["OK"], "scores": [0.9], "is_confident": True,
        }
        mock_client_good = AsyncMock()
        mock_client_good.post = AsyncMock(return_value=mock_response)
        engine._pool_clients[1] = mock_client_good

        result = await engine._http_predict("fake_b64")
        assert engine._pool_healthy[0] is False
        assert engine._pool_healthy[1] is True
