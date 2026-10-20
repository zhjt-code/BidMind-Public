"""OCR Sidecar 服务

独立容器运行 PaddleOCR GPU 推理，通过 HTTP 接口与 backend 通信。
与 backend 容器物理隔离，避免 paddlepaddle-gpu 与 torch 的 cuDNN 冲突。

接口:
  POST /predict  -- 接收 base64 图片，返回 OCR 识别结果
  GET  /health   -- 健康检查
"""

import base64
import io
import os
import threading
import time
import traceback
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from loguru import logger
from PIL import Image
from pydantic import BaseModel, Field

# ── 配置 ──────────────────────────────────────────────────────────────

OCR_PIPELINE = os.getenv("OCR_PIPELINE", "ocr").strip().lower()
OCR_LANG = os.getenv("OCR_LANG", "ch")
OCR_VERSION = os.getenv("OCR_VERSION", "PP-OCRv4")
# 显式模型名（二者同时非空时优先生效）：用于锁定 PP-OCRv5_server 等具体变体，
# 因为仅传 ocr_version 不保证加载 server 模型，可能静默回退到 mobile。
OCR_DET_MODEL_NAME = os.getenv("OCR_DET_MODEL_NAME", "")
OCR_REC_MODEL_NAME = os.getenv("OCR_REC_MODEL_NAME", "")
OCR_CONFIDENCE_THRESHOLD = float(os.getenv("OCR_CONFIDENCE_THRESHOLD", "0.75"))
OCR_MIN_TEXT_LENGTH = int(os.getenv("OCR_MIN_TEXT_LENGTH", "20"))
STRUCTURE_STATS_EVERY = max(1, int(os.getenv("STRUCTURE_STATS_EVERY", "20")))
STRUCTURE_MAX_CONSECUTIVE_FAILURES = int(os.getenv("STRUCTURE_MAX_CONSECUTIVE_FAILURES", "3"))


def _exit_for_restart() -> None:
    """延迟退出, 让本次 503 先返回。compose 的 restart 策略会拉起新进程。"""
    threading.Timer(1.0, os._exit, (1,)).start()


# ── PaddleOCR 引擎 ───────────────────────────────────────────────────

class _OcrEngine:
    """PaddleOCR 封装，懒加载 + 推理锁"""

    def __init__(self) -> None:
        self._ocr = None
        self._init_lock = threading.Lock()
        self._predict_lock = threading.Lock()
        self._init_error: Optional[str] = None

    def ensure_initialized(self) -> bool:
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
                # 默认走百度 BOS 拉模型：ECS 连不上 HuggingFace 时，
                # 可跳过 HF→aistudio→modelscope 的失败级联，首启从十几分钟降到 1-2 分钟。
                # 仍可通过环境变量覆盖为其他源。
                os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
                from paddleocr import PaddleOCR

                ocr_kwargs = {"use_textline_orientation": True}
                use_explicit_models = bool(
                    OCR_DET_MODEL_NAME and OCR_REC_MODEL_NAME
                )
                if use_explicit_models:
                    # 显式模型名与 lang/ocr_version 互斥：rec 模型本身决定语种与版本，
                    # 同时传 lang 可能与所选模型冲突，故此分支不传 lang/ocr_version。
                    ocr_kwargs["text_detection_model_name"] = OCR_DET_MODEL_NAME
                    ocr_kwargs["text_recognition_model_name"] = OCR_REC_MODEL_NAME
                else:
                    ocr_kwargs["lang"] = OCR_LANG
                    ocr_kwargs["ocr_version"] = OCR_VERSION

                self._ocr = PaddleOCR(**ocr_kwargs)
                import paddle
                device = paddle.get_device()
                if use_explicit_models:
                    model_desc = (
                        f"det={OCR_DET_MODEL_NAME}, rec={OCR_REC_MODEL_NAME}"
                    )
                else:
                    model_desc = f"lang={OCR_LANG}, version={OCR_VERSION}"
                logger.info(
                    f"[OCR服务] 初始化成功: {model_desc}, device={device}"
                )
                return True
            except Exception as e:
                self._init_error = str(e)
                logger.error(f"[OCR服务] 初始化失败: {e}")
                return False

    def predict(self, image_array: np.ndarray) -> dict:
        """执行 OCR 推理，返回原始结果字典"""
        if not self.ensure_initialized():
            return {}

        with self._predict_lock:
            results = self._ocr.predict(image_array)

        if not results:
            return {}

        item = results[0]
        return {
            "rec_texts": list(item.get("rec_texts", [])),
            "rec_scores": [float(s) for s in item.get("rec_scores", [])],
        }


_engine = _OcrEngine()
_structure_engine = None
if OCR_PIPELINE == "structure":
    from structure_engine import StructureEngine

    _structure_engine = StructureEngine()


# ── 请求/响应模型 ────────────────────────────────────────────────────

class PredictRequest(BaseModel):
    image_b64: str = Field(..., description="Base64 encoded PNG/JPEG image")


class PredictResponse(BaseModel):
    raw_text: str = ""
    avg_confidence: float = 0.0
    texts: list[str] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    is_confident: bool = False


# ── FastAPI 应用 ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"[OCR服务] 启动中: pipeline={OCR_PIPELINE}")
    if _structure_engine is not None:
        _structure_engine.ensure_initialized()
    else:
        _engine.ensure_initialized()
    yield
    logger.info("[OCR服务] 关闭")


app = FastAPI(title="BidMind OCR Service", lifespan=lifespan)


class StructureRequest(BaseModel):
    image_b64: str = Field(..., description="Base64 encoded page image")


class StructureResponse(BaseModel):
    blocks: list[dict] = Field(default_factory=list)


@app.get("/health")
def health():
    if _structure_engine is not None:
        ready = _structure_engine.ensure_initialized()
        if not ready:
            raise HTTPException(status_code=503, detail="structure pipeline is not initialized")
        return {"status": "ok", "structure_ready": True, "ocr_ready": False}
    initialized = _engine.ensure_initialized()
    return {"status": "ok" if initialized else "degraded", "ocr_ready": initialized}


@app.post("/structure", response_model=StructureResponse)
def structure(req: StructureRequest):
    if _structure_engine is None:
        raise HTTPException(status_code=404, detail="structure pipeline is not enabled")
    try:
        image_bytes = base64.b64decode(req.image_b64)
        pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        np_img = np.array(pil_img)
    except Exception as exc:
        logger.warning(f"[版面服务] 图片解码失败: {exc}")
        return StructureResponse()
    if not _structure_engine.ensure_initialized():
        raise HTTPException(status_code=503, detail="structure sidecar is not initialized")
    try:
        blocks = _structure_engine.predict(np_img)
    except Exception as exc:
        # 纯调用栈: 不带局部变量值, 避免把页面 base64 写进日志
        logger.warning(
            f"[版面服务] 推理异常: type={type(exc).__name__}, msg={exc!r:.300}, "
            f"image={np_img.shape[1]}x{np_img.shape[0]}, {_structure_engine.stats()}\n"
            f"{''.join(traceback.format_exception(exc))}"
        )
        if (
            STRUCTURE_MAX_CONSECUTIVE_FAILURES > 0
            and _structure_engine.consecutive_failures >= STRUCTURE_MAX_CONSECUTIVE_FAILURES
        ):
            logger.error(
                f"[版面服务] 连续失败 {_structure_engine.consecutive_failures} 次, 退出以便重启"
            )
            _exit_for_restart()
        raise HTTPException(status_code=503, detail="structure inference failed") from exc
    if _structure_engine.pages_ok % STRUCTURE_STATS_EVERY == 0:
        logger.info(f"[版面服务] 运行统计: {_structure_engine.stats()}")
    return StructureResponse(blocks=blocks)


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    if _structure_engine is not None:
        raise HTTPException(status_code=404, detail="ocr predict is disabled on structure pipeline")
    start = time.monotonic()

    try:
        image_bytes = base64.b64decode(req.image_b64)
        pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        np_img = np.array(pil_img)
    except Exception as e:
        logger.warning(f"[OCR服务] 图片解码失败: {e}")
        return PredictResponse()

    if not _engine.ensure_initialized():
        logger.warning("[OCR服务] 未初始化，返回503")
        raise HTTPException(
            status_code=503,
            detail="OCR sidecar is not initialized",
        )

    try:
        result = _engine.predict(np_img)
    except Exception as e:
        logger.warning(f"[OCR服务] 推理异常: {e}")
        raise HTTPException(
            status_code=503,
            detail="OCR sidecar inference failed",
        )

    texts = result.get("rec_texts", [])
    scores = result.get("rec_scores", [])

    if not texts:
        return PredictResponse()

    raw_text = "\n".join(texts)
    avg_conf = sum(scores) / len(scores) if scores else 0.0
    is_confident = (
        avg_conf >= OCR_CONFIDENCE_THRESHOLD
        and len(raw_text) >= OCR_MIN_TEXT_LENGTH
    )

    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        f"[OCR服务] 推理完成: texts={len(texts)}, "
        f"conf={avg_conf:.2f}, confident={is_confident}, "
        f"elapsed={elapsed_ms:.0f}ms"
    )

    return PredictResponse(
        raw_text=raw_text,
        avg_confidence=avg_conf,
        texts=texts,
        scores=scores,
        is_confident=is_confident,
    )
