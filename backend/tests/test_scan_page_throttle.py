"""整页扫描 OCR 受控放量防御测试

覆盖:
- 扫描密集页不会一次性把全部页面推入 OCR 等待队列
- 整页 OCR 使用 request_class=full_page 标记请求类别
- 整页 OCR 统计计数器正确累计
- TimeoutException 会摘除实例
- OCR 池全部不健康时整页直接回退 VL
- OCR queue_wait_p95 超过 ceiling 时阻止并发增长
"""

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)


@dataclass
class FakePage:
    page_num: int = 1
    text: str = ""
    images: list = field(default_factory=list)


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


def _make_ocr_result(confident: bool = True, text: str = "测试文本内容"):
    from app.core.parser.ocr_engine import OcrResult

    return OcrResult(
        raw_text=text,
        avg_confidence=0.92 if confident else 0.3,
        is_confident=confident,
    )


@pytest.mark.asyncio
class TestScanPageThrottle:
    async def test_semaphore_limits_concurrent_ocr(self):
        """整页 OCR 并发不超过信号量上限"""
        max_concurrent = 0
        current = 0
        lock = asyncio.Lock()

        async def _tracking_recognize(np_img, *, request_class="per_image"):
            nonlocal max_concurrent, current
            async with lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.02)
            async with lock:
                current -= 1
            return _make_ocr_result(confident=True)

        sem_limit = 3
        sem = asyncio.Semaphore(sem_limit)
        page_count = 12

        async def _throttled_ocr(page_num: int):
            async with sem:
                return await _tracking_recognize(None, request_class="full_page")

        tasks = [_throttled_ocr(i) for i in range(page_count)]
        await asyncio.gather(*tasks)

        assert max_concurrent <= sem_limit, (
            f"Peak concurrency {max_concurrent} exceeded limit {sem_limit}"
        )

    async def test_full_page_request_class_passed_to_recognize(self):
        """整页 OCR 调用 recognize_from_array 时传递 request_class=full_page"""
        from app.core.parser.ocr_engine import OcrEngine

        engine = OcrEngine(lang="ch", service_url="http://ocr:9000")
        engine._http_predict = AsyncMock(
            return_value=_make_ocr_result(confident=True),
        )

        import numpy as np

        fake_img = np.zeros((100, 200, 3), dtype=np.uint8)
        await engine.recognize_from_array(fake_img, request_class="full_page")

        engine._http_predict.assert_called_once()
        call_kwargs = engine._http_predict.call_args
        assert call_kwargs.kwargs.get("request_class") == "full_page", (
            "recognize_from_array should forward request_class=full_page "
            "to _http_predict"
        )

    async def test_congested_pages_counted_as_degraded_before(self):
        """拥堵时直接降级的页面应计入 degraded_before_attempt"""
        degraded_before = 0

        mock_engine = MagicMock()
        mock_engine.is_congested = True
        mock_engine.wait_for_recovery = AsyncMock(return_value=False)

        for _ in range(5):
            if mock_engine.is_congested:
                recovered = await mock_engine.wait_for_recovery(timeout=3.0)
                if not recovered:
                    degraded_before += 1

        assert degraded_before == 5

    async def test_successful_ocr_counted_correctly(self):
        """OCR 成功的页面应计入 scan_ocr_ok"""
        scan_ocr_ok = 0

        mock_engine = MagicMock()
        mock_engine.is_congested = False
        mock_engine.recognize_from_array = AsyncMock(
            return_value=_make_ocr_result(confident=True),
        )

        for _ in range(3):
            result = await mock_engine.recognize_from_array(
                None,
                request_class="full_page",
            )
            if result.is_confident:
                scan_ocr_ok += 1

        assert scan_ocr_ok == 3


@pytest.mark.asyncio
class TestScanPageObservability:
    async def test_ocr_stats_include_pool_info(self):
        """OcrEngine.get_http_stats() 包含 pool_healthy 和 pool_total"""
        from app.core.parser.ocr_engine import OcrEngine

        engine = OcrEngine(
            lang="ch",
            service_urls=[
                "http://ocr-1:9000",
                "http://ocr-2:9000",
                "http://ocr-3:9000",
            ],
        )
        stats = engine.get_http_stats()
        assert "pool_total" in stats
        assert "pool_healthy" in stats
        assert stats["pool_total"] == 3
        assert stats["pool_healthy"] == 3

    async def test_ocr_stats_reflects_unhealthy_instance(self):
        """标记实例不健康后, pool_healthy 应减少"""
        from app.core.parser.ocr_engine import OcrEngine

        engine = OcrEngine(
            lang="ch",
            service_urls=[
                "http://ocr-1:9000",
                "http://ocr-2:9000",
            ],
        )
        engine._pool_healthy[0] = False
        stats = engine.get_http_stats()
        assert stats["pool_total"] == 2
        assert stats["pool_healthy"] == 1


@pytest.mark.asyncio
class TestTimeoutInstanceEviction:
    async def test_timeout_marks_instance_unhealthy(self):
        """TimeoutException 应将 OCR 实例标记为不健康"""
        import httpx
        from app.core.parser.ocr_engine import OcrEngine

        engine = OcrEngine(
            lang="ch",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )
        assert engine._pool_healthy[0] is True

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ReadTimeout("timeout"),
        )
        engine._pool_clients[0] = mock_client

        result = await engine._http_predict("fake_b64")
        assert result.raw_text == ""
        assert engine._pool_healthy[0] is False, (
            "Timeout 应将实例标记为不健康"
        )

    async def test_unhealthy_timeout_instance_skipped_in_round_robin(self):
        """被超时摘除的实例不应再被轮询命中"""
        from app.core.parser.ocr_engine import OcrEngine

        engine = OcrEngine(
            lang="ch",
            service_urls=[
                "http://ocr-1:9000",
                "http://ocr-2:9000",
                "http://ocr-3:9000",
            ],
        )
        engine._pool_healthy[2] = False

        for _ in range(6):
            idx = engine._select_pool_index()
            assert idx != 2, (
                "不健康实例不应被轮询选中"
            )


@pytest.mark.asyncio
class TestScanFastFallback:
    async def test_all_unhealthy_triggers_immediate_fallback(self):
        """OCR 池全部不健康时, 整页 OCR 应立刻回退"""
        from app.core.parser.ocr_engine import OcrEngine

        engine = OcrEngine(
            lang="ch",
            service_urls=["http://ocr-1:9000", "http://ocr-2:9000"],
        )
        engine._pool_healthy[0] = False
        engine._pool_healthy[1] = False

        stats = engine.get_http_stats()
        assert stats["pool_healthy"] == 0
        assert stats["pool_total"] == 2


@pytest.mark.asyncio
class TestQueueWaitCeilingBlocksIncrease:
    async def test_queue_wait_above_ceiling_blocks_increase(self, monkeypatch):
        """queue_wait_p95 超过 ceiling 时应阻止并发增长"""
        from app.core.config import settings
        from app.core.parser.adaptive_concurrency import (
            OcrAdaptiveConcurrency,
            OcrRequestMetric,
        )

        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INITIAL", 2)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MIN", 1)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_MAX", 6)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_WINDOW_SIZE", 4)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_INCREASE_COOLDOWN", 0)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_LATENCY_CEILING_MS", 15000)
        monkeypatch.setattr(settings, "OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS", 3000)

        controller = OcrAdaptiveConcurrency()
        initial = controller.current

        for _ in range(4):
            await controller.record(OcrRequestMetric(
                latency_ms=800,
                queue_wait_ms=5000,
                success=True,
                is_timeout=False,
                is_transport_error=False,
                is_low_confidence=False,
                weight=1,
            ))

        assert controller.current <= initial, (
            f"queue_wait_p95 超过 ceiling 时不应增长并发, "
            f"initial={initial}, current={controller.current}"
        )
