"""OCR 引擎 -- 双模式客户端

支持两种运行模式:
1. HTTP sidecar 模式 (service_url 非空): 通过 HTTP 调用独立 OCR 容器
   - paddlepaddle-gpu 在 OCR 容器中运行，与 torch 完全隔离
   - GPU 加速，推理 ~0.1-0.3s/页
2. 本地进程内模式 (service_url 为空): 直接调用 PaddleOCR CPU
   - 向后兼容，无需 Docker 即可开发调试
   - CPU 推理 ~2-3s/页

上层调用接口 (recognize / recognize_from_array) 签名和返回类型完全一致，
image_analyzer / process_task / document_service 零改动。
"""

import asyncio
import base64
import io
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

from app.core.parser.adaptive_concurrency import (
    OcrAdaptiveConcurrency,
    OcrRequestMetric,
)
from app.core.parser.ocr_flow_scheduler import OcrFlowScheduler


@dataclass
class OcrTextRegion:
    """OCR 识别的单个文本区域"""
    text: str
    confidence: float
    bbox: list = field(default_factory=list)


@dataclass
class OcrResult:
    """OCR 识别结果"""
    raw_text: str
    avg_confidence: float
    regions: list[OcrTextRegion] = field(default_factory=list)
    is_confident: bool = False


_EMPTY_RESULT = OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

# HTTP 默认配置
_DEFAULT_HTTP_TIMEOUT = 30.0
_DEFAULT_HTTP_MAX_CONCURRENCY = 1
_DEFAULT_QUEUE_WARN_MS = 5000
_QUEUE_WARN_LOG_COOLDOWN_SEC = 10.0


_HEALTH_CHECK_TIMEOUT = 5.0

# 客户端连接池空闲过期必须显式小于服务端 uvicorn --timeout-keep-alive(75s,
# 见 ocr/Dockerfile), 否则两侧同吃 5s 默认值会产生"服务端刚关闭闲置连接/客户端刚复用"
# 的陈旧连接竞态, 表现为偶发 ReadError('') 且该页 OCR 结果静默丢失。
_CLIENT_KEEPALIVE_EXPIRY_SEC = 30.0


async def _post_predict_with_stale_retry(client, image_b64: str, target_url: str):
    """向 /predict 发送请求, 遇陈旧连接类传输错误换新连接重试一次。

 职责: 只兜底"keep-alive 陈旧连接"这一类可安全重试的传输错误; /predict 幂等, 重试无副作用。
 不做: 不重试超时/连接失败(由调用方独立分支处理并摘除实例)、不解析响应、不管理实例健康。
 """
    import httpx

    stale_errors = (
        httpx.ReadError,
        httpx.WriteError,
        httpx.CloseError,
        httpx.RemoteProtocolError,
    )
    try:
        return await client.post("/predict", json={"image_b64": image_b64})
    except stale_errors as e:
        logger.info(
            f"[OcrEngine] 检测到陈旧连接错误, 换新连接重试一次: "
            f"url={target_url}, error={type(e).__name__}"
        )
        return await client.post("/predict", json={"image_b64": image_b64})


class OcrEngine:
    """OCR 双模式客户端，单例懒加载，支持多实例池

    Args:
        lang: 识别语言 (ch=中英混合)
        ocr_version: OCR 模型版本
        confidence_threshold: 置信度阈值
        min_text_length: 最小有效文字长度
        service_url: OCR sidecar HTTP 地址，空串走本地模式
        service_urls: 多实例地址列表，非空时覆盖 service_url
        http_timeout: OCR sidecar HTTP 超时（秒）
        http_max_concurrency: OCR sidecar 客户端并发上限
        queue_warn_ms: OCR 客户端排队告警阈值（毫秒）
    """

    _instance: Optional["OcrEngine"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        lang: str = "ch",
        ocr_version: str = "PP-OCRv4",
        confidence_threshold: float = 0.75,
        min_text_length: int = 20,
        service_url: str = "",
        service_urls: Optional[list[str]] = None,
        http_timeout: float = _DEFAULT_HTTP_TIMEOUT,
        http_max_concurrency: int = _DEFAULT_HTTP_MAX_CONCURRENCY,
        queue_warn_ms: int = _DEFAULT_QUEUE_WARN_MS,
    ) -> None:
        self._lang = lang
        self._ocr_version = ocr_version
        self._confidence_threshold = confidence_threshold
        self._min_text_length = min_text_length
        self._http_timeout = max(float(http_timeout), 1.0)
        self._http_max_concurrency = max(int(http_max_concurrency), 1)
        self._queue_warn_ms = max(int(queue_warn_ms), 0)

        # 构建 URL 列表: service_urls 优先于 service_url
        if service_urls:
            self._service_urls = [u.rstrip("/") for u in service_urls if u.strip()]
        elif service_url:
            self._service_urls = [service_url.rstrip("/")]
        else:
            self._service_urls = []
        self._service_url = self._service_urls[0] if self._service_urls else ""
        self._mode = "http" if self._service_urls else "local"

        # 本地模式专用
        self._ocr = None
        self._init_lock = threading.Lock()
        self._predict_lock = threading.Lock()
        self._init_error: Optional[str] = None

        # HTTP 实例池
        self._pool_clients: list[Optional[object]] = [None] * len(self._service_urls)
        self._pool_healthy: list[bool] = [True] * len(self._service_urls)
        self._pool_inflight: list[int] = [0] * len(self._service_urls)
        self._pool_last_health: list[float] = [0.0] * len(self._service_urls)
        self._pool_index = 0
        self._pool_lock = threading.Lock()

        self._http_controller: Optional[OcrAdaptiveConcurrency] = None
        self._http_controller_loop = None
        from app.core.config import settings
        self._flow_scheduler = OcrFlowScheduler(
            contended_max_share_ratio=settings.OCR_CONTENDED_MAX_SHARE_RATIO,
        )
        self._last_queue_warn_time = 0.0

        if self._mode == "http":
            urls_str = ", ".join(self._service_urls)
            logger.info(
                f"[OcrEngine] 模式: http, 实例数={len(self._service_urls)}, "
                f"urls=[{urls_str}], timeout={self._http_timeout}s, "
                f"max_concurrency={self._http_max_concurrency}"
            )
        else:
            logger.info("[OcrEngine] 模式: local")

    @classmethod
    def get_instance(cls, **kwargs) -> "OcrEngine":
        """获取全局单例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅测试用）"""
        with cls._lock:
            if cls._instance:
                for client in cls._instance._pool_clients:
                    if client is not None:
                        try:
                            import asyncio as _aio
                            loop = _aio.get_event_loop()
                            if loop.is_running():
                                loop.create_task(client.aclose())
                            else:
                                loop.run_until_complete(client.aclose())
                        except Exception:
                            pass
            cls._instance = None

    # ── HTTP 实例池 ──────────────────────────────────────────────────

    def _get_pool_client(self, index: int):
        """懒初始化指定下标的 httpx AsyncClient"""
        if self._pool_clients[index] is None:
            import httpx
            self._pool_clients[index] = httpx.AsyncClient(
                base_url=self._service_urls[index],
                timeout=httpx.Timeout(self._http_timeout),
                # 客户端先于服务端(75s)弃置闲置连接, 消除陈旧连接竞态
                limits=httpx.Limits(keepalive_expiry=_CLIENT_KEEPALIVE_EXPIRY_SEC),
            )
        return self._pool_clients[index]

    def _select_pool_index(self) -> int:
        """选择当前在飞请求最少的健康实例，平局时轮询打散"""
        with self._pool_lock:
            n = len(self._service_urls)
            if n == 0:
                return -1
            healthy_indices = [i for i, healthy in enumerate(self._pool_healthy) if healthy]
            if not healthy_indices:
                return -1
            min_inflight = min(self._pool_inflight[i] for i in healthy_indices)
            for _ in range(n):
                idx = self._pool_index % n
                self._pool_index += 1
                if self._pool_healthy[idx] and self._pool_inflight[idx] == min_inflight:
                    return idx
        return -1

    def _next_pool_index(self) -> int:
        """兼容旧测试/调用方: 返回最小在飞实例选择结果。"""
        return self._select_pool_index()

    def _increment_pool_inflight(self, index: int) -> None:
        """记录指定实例新增一个在飞请求"""
        with self._pool_lock:
            self._pool_inflight[index] += 1

    def _decrement_pool_inflight(self, index: int) -> None:
        """记录指定实例完成一个在飞请求"""
        with self._pool_lock:
            self._pool_inflight[index] = max(0, self._pool_inflight[index] - 1)

    def _get_http_controller(self) -> OcrAdaptiveConcurrency:
        """按事件循环懒初始化 OCR 自适应并发控制器，并发上限随实例数缩放"""
        loop = asyncio.get_running_loop()
        if self._http_controller is None or self._http_controller_loop is not loop:
            self._http_controller = OcrAdaptiveConcurrency(
                instance_count=len(self._service_urls),
            )
            self._http_controller_loop = loop
        return self._http_controller

    async def _try_recover_unhealthy(self) -> None:
        """尝试恢复不健康实例"""
        from app.core.config import settings
        interval = settings.OCR_POOL_HEALTH_INTERVAL
        now = time.monotonic()
        for i, url in enumerate(self._service_urls):
            if self._pool_healthy[i]:
                continue
            if now - self._pool_last_health[i] < interval:
                continue
            self._pool_last_health[i] = now
            try:
                client = self._get_pool_client(i)
                resp = await client.get("/health", timeout=_HEALTH_CHECK_TIMEOUT)
                if resp.status_code == 200:
                    self._pool_healthy[i] = True
                    logger.info(f"[OcrEngine] 实例已恢复: {url}")
            except Exception:
                pass

    async def _record_http_failure(
        self,
        controller: OcrAdaptiveConcurrency,
        *,
        elapsed_ms: float,
        waited_ms: float,
        is_timeout: bool = False,
        is_transport_error: bool = True,
        request_class: str = "per_image",
    ) -> None:
        """记录一次 OCR HTTP 失败指标"""
        await controller.record(
            OcrRequestMetric(
                latency_ms=elapsed_ms,
                queue_wait_ms=waited_ms,
                success=False,
                is_timeout=is_timeout,
                is_transport_error=is_transport_error,
                is_low_confidence=False,
                weight=1,
                request_class=request_class,
            )
        )

    def _warn_if_queued_too_long(
        self,
        controller: OcrAdaptiveConcurrency,
        waited_ms: float,
        target_url: str,
    ) -> None:
        """按冷却时间输出 OCR 客户端排队告警"""
        now = time.monotonic()
        if (
            not self._queue_warn_ms
            or waited_ms < self._queue_warn_ms
            or now - self._last_queue_warn_time < _QUEUE_WARN_LOG_COOLDOWN_SEC
        ):
            return
        healthy = sum(1 for h in self._pool_healthy if h)
        logger.warning(
            "[OcrEngine] HTTP 调用排队过久: "
            f"waited_ms={waited_ms:.0f}, "
            f"concurrency={controller.current}, "
            f"congested={controller.is_congested}, "
            f"window_fill={len(controller._metrics)}/{controller._window_size}, "
            f"pool={healthy}/{len(self._service_urls)}, "
            f"inflight={self._pool_inflight}, "
            f"url={target_url}"
        )
        self._last_queue_warn_time = now

    async def _build_success_result(
        self,
        controller: OcrAdaptiveConcurrency,
        data: dict,
        *,
        waited_ms: float,
        request_start: float,
        request_class: str,
    ) -> OcrResult:
        """解析 OCR sidecar 成功响应并写入 AIMD 度量"""
        texts = data.get("texts", [])
        scores = data.get("scores", [])
        raw_text = data.get("raw_text", "")
        avg_conf = float(data.get("avg_confidence", 0.0))
        is_confident = bool(data.get("is_confident", False))
        regions = [
            OcrTextRegion(text=t, confidence=s)
            for t, s in zip(texts, scores)
        ]

        elapsed_ms = (time.monotonic() - request_start) * 1000
        await controller.record(
            OcrRequestMetric(
                latency_ms=elapsed_ms,
                queue_wait_ms=waited_ms,
                success=True,
                is_timeout=False,
                is_transport_error=False,
                is_low_confidence=not is_confident,
                weight=1,
                request_class=request_class,
            )
        )

        return OcrResult(
            raw_text=raw_text,
            avg_confidence=avg_conf,
            regions=regions,
            is_confident=is_confident,
        )

    async def _http_predict(
        self,
        image_b64: str,
        *,
        request_class: str = "per_image",
        flow_id: str = "default",
    ) -> OcrResult:
        """通过 HTTP 调用 OCR sidecar 服务（支持多实例池轮询）"""

        await self._try_recover_unhealthy()

        controller = self._get_http_controller()
        try:
            async with self._flow_scheduler.slot(
                flow_id,
                request_class,
                capacity=controller.current,
            ):
                queue_start = time.monotonic()
                async with controller.weighted(1):
                    return await self._send_http_predict(
                        image_b64,
                        controller=controller,
                        queue_start=queue_start,
                        request_class=request_class,
                    )
        except Exception as e:
            logger.warning(
                f"[OcrEngine] 流程公平调度异常，返回空结果: "
                f"flow_id={flow_id}, request_class={request_class}, "
                f"error={type(e).__name__}: {e!r}"
            )
            return _EMPTY_RESULT

    async def _send_http_predict(
        self,
        image_b64: str,
        *,
        controller: OcrAdaptiveConcurrency,
        queue_start: float,
        request_class: str,
    ) -> OcrResult:
        """在已获取公平槽和 AIMD 槽后发送 OCR HTTP 请求"""
        import httpx

        pool_idx = self._select_pool_index()
        if pool_idx < 0:
            logger.warning("[OcrEngine] 所有 OCR 实例不健康，返回空结果")
            return _EMPTY_RESULT
        self._increment_pool_inflight(pool_idx)
        target_url = self._service_urls[pool_idx]
        waited_ms = (time.monotonic() - queue_start) * 1000
        self._warn_if_queued_too_long(controller, waited_ms, target_url)

        request_start = time.monotonic()
        try:
            client = self._get_pool_client(pool_idx)
            resp = await _post_predict_with_stale_retry(client, image_b64, target_url)
            resp.raise_for_status()
            data = resp.json()
            result = await self._build_success_result(
                controller,
                data,
                waited_ms=waited_ms,
                request_start=request_start,
                request_class=request_class,
            )
            return result
        except httpx.TimeoutException as e:
            elapsed_ms = (time.monotonic() - request_start) * 1000
            self._pool_healthy[pool_idx] = False
            logger.warning(
                f"[OcrEngine] HTTP 超时，实例已摘除: "
                f"waited_ms={waited_ms:.0f}, elapsed_ms={elapsed_ms:.0f}, "
                f"url={target_url}, error={type(e).__name__}"
            )
            await self._record_http_failure(
                controller,
                elapsed_ms=elapsed_ms,
                waited_ms=waited_ms,
                is_timeout=True,
                is_transport_error=False,
                request_class=request_class,
            )
        except httpx.ConnectError as e:
            elapsed_ms = (time.monotonic() - request_start) * 1000
            self._pool_healthy[pool_idx] = False
            logger.warning(
                f"[OcrEngine] 连接失败，实例已摘除: url={target_url}, "
                f"error={type(e).__name__}"
            )
            await self._record_http_failure(
                controller,
                elapsed_ms=elapsed_ms,
                waited_ms=waited_ms,
                request_class=request_class,
            )
        except httpx.HTTPStatusError as e:
            elapsed_ms = (time.monotonic() - request_start) * 1000
            if e.response.status_code >= 500:
                self._pool_healthy[pool_idx] = False
            logger.warning(
                f"[OcrEngine] HTTP 状态异常: url={target_url}, "
                f"status={e.response.status_code}"
            )
            await self._record_http_failure(
                controller,
                elapsed_ms=elapsed_ms,
                waited_ms=waited_ms,
                request_class=request_class,
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - request_start) * 1000
            logger.warning(
                f"[OcrEngine] HTTP 调用失败: url={target_url}, "
                f"error={type(e).__name__}: {e!r}"
            )
            await self._record_http_failure(
                controller,
                elapsed_ms=elapsed_ms,
                waited_ms=waited_ms,
                request_class=request_class,
            )
        finally:
            self._decrement_pool_inflight(pool_idx)
        return _EMPTY_RESULT

    # ── 本地模式 ─────────────────────────────────────────────────────

    def _ensure_initialized(self) -> bool:
        """懒加载本地 PaddleOCR 实例"""
        if self._ocr is not None:
            return True
        if self._init_error is not None:
            return False

        with self._init_lock:
            if self._ocr is not None:
                return True
            if self._init_error is not None:
                return False

            try:
                os.environ.setdefault(
                    "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True"
                )
                from paddleocr import PaddleOCR

                self._ocr = PaddleOCR(
                    lang=self._lang,
                    ocr_version=self._ocr_version,
                    use_textline_orientation=True,
                )
                import paddle
                device = paddle.get_device()
                logger.info(
                    f"[OcrEngine] 本地初始化成功: lang={self._lang}, "
                    f"version={self._ocr_version}, device={device}"
                )
                return True
            except Exception as e:
                self._init_error = str(e)
                logger.error(f"[OcrEngine] 本地初始化失败: {e}")
                return False

    def _parse_predict_result(self, results: list) -> OcrResult:
        """从 PaddleOCR predict() 返回值中解析 OcrResult"""
        if not results:
            return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

        item = results[0]
        rec_texts = item.get("rec_texts", [])
        rec_scores = item.get("rec_scores", [])
        dt_polys = item.get("dt_polys", [])

        if not rec_texts:
            return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

        regions: list[OcrTextRegion] = []
        for i, text in enumerate(rec_texts):
            conf = float(rec_scores[i]) if i < len(rec_scores) else 0.0
            bbox = dt_polys[i].tolist() if i < len(dt_polys) else []
            regions.append(OcrTextRegion(text=text, confidence=conf, bbox=bbox))

        raw_text = "\n".join(rec_texts)
        avg_conf = (
            sum(float(s) for s in rec_scores) / len(rec_scores)
            if rec_scores
            else 0.0
        )
        is_confident = (
            avg_conf >= self._confidence_threshold
            and len(raw_text) >= self._min_text_length
        )

        return OcrResult(
            raw_text=raw_text,
            avg_confidence=avg_conf,
            regions=regions,
            is_confident=is_confident,
        )

    def _recognize_sync(self, image_path: str) -> OcrResult:
        """本地模式: 同步识别单张图片"""
        if not self._ensure_initialized():
            return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

        path = Path(image_path)
        if not path.exists():
            logger.warning(f"[OcrEngine] 图片不存在: {image_path}")
            return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

        try:
            with self._predict_lock:
                results = self._ocr.predict(str(path))
            return self._parse_predict_result(results)
        except Exception as e:
            logger.warning(f"[OcrEngine] 本地识别异常: {image_path}, error={e}")
            return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

    def _recognize_from_array_sync(self, image_array: np.ndarray) -> OcrResult:
        """本地模式: 同步识别 numpy array"""
        if not self._ensure_initialized():
            return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

        if image_array is None or image_array.size == 0:
            return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

        try:
            with self._predict_lock:
                results = self._ocr.predict(image_array)
            return self._parse_predict_result(results)
        except Exception as e:
            logger.warning(f"[OcrEngine] 本地numpy识别异常: error={e}")
            return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)

    # ── 统一公开接口 ─────────────────────────────────────────────────

    async def recognize(
        self,
        image_path: str,
        *,
        request_class: str = "per_image",
        flow_id: str = "default",
    ) -> OcrResult:
        """异步识别单张图片（文件路径）"""
        if self._mode == "http":
            path = Path(image_path)
            if not path.exists():
                logger.warning(f"[OcrEngine] 图片不存在: {image_path}")
                return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)
            with open(path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("ascii")
            return await self._http_predict(
                image_b64,
                request_class=request_class,
                flow_id=flow_id,
            )
        return await asyncio.to_thread(self._recognize_sync, image_path)

    async def recognize_from_array(
        self,
        image_array: np.ndarray,
        *,
        request_class: str = "per_image",
        flow_id: str = "default",
    ) -> OcrResult:
        """异步识别 numpy array 图片"""
        if self._mode == "http":
            if image_array is None or image_array.size == 0:
                return OcrResult(raw_text="", avg_confidence=0.0, is_confident=False)
            from PIL import Image
            pil_img = Image.fromarray(image_array)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return await self._http_predict(
                image_b64,
                request_class=request_class,
                flow_id=flow_id,
            )
        return await asyncio.to_thread(self._recognize_from_array_sync, image_array)

    async def recognize_batch(self, image_paths: list[str]) -> list[OcrResult]:
        """异步批量识别图片"""
        results: list[OcrResult] = []
        for path in image_paths:
            r = await self.recognize(path)
            results.append(r)
        return results

    @property
    def is_congested(self) -> bool:
        """OCR HTTP 队列是否拥堵，供路由降级决策使用"""
        if self._mode != "http" or self._http_controller is None:
            return False
        return self._http_controller.is_congested

    async def wait_for_recovery(self, timeout: float = 3.0) -> bool:
        """等待 OCR 拥堵恢复，供竞争路由和整页 OCR 使用

        Returns:
            True 表示已恢复或本就不拥堵，False 表示超时仍拥堵
        """
        if self._mode != "http" or self._http_controller is None:
            return True
        return await self._http_controller.wait_for_recovery(timeout)

    def get_http_stats(self) -> dict:
        """返回 HTTP OCR 控制器统计，供心跳与验证使用"""
        if self._mode != "http" or self._http_controller is None:
            return {
                "mode": self._mode,
                "is_congested": False,
                "concurrency": 0,
                "window_fill": 0,
                "pool_total": len(self._service_urls),
                "pool_healthy": sum(1 for h in self._pool_healthy if h),
                "pool_inflight": list(self._pool_inflight),
                "flow_scheduler": self._flow_scheduler.get_stats(),
            }
        stats = self._http_controller.get_stats()
        return {
            "mode": self._mode,
            **stats,
            "pool_total": len(self._service_urls),
            "pool_healthy": sum(1 for h in self._pool_healthy if h),
            "pool_inflight": list(self._pool_inflight),
            "flow_scheduler": self._flow_scheduler.get_stats(),
        }
