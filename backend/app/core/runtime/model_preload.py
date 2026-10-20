"""生产模型预热运行时。

职责: 仅在生产 Worker 启动阶段预热本进程即将使用的 OCR/embedding 运行时依赖。
不做: 不改变业务任务调度、不替代健康检查路由、不在本地开发环境强制加载模型。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.core.config import settings

_PROCESS_WORKER_ROLE = "process"
_PRELOAD_PROBE_TEXT = "模型预热"
_DEFAULT_TIMEOUT_SEC = 180.0


@dataclass(frozen=True)
class ModelPreloadConfig:
    """生产模型预热配置。"""

    enabled: bool
    block_ready: bool
    warmup_ocr: bool
    warmup_embedding: bool
    timeout_sec: float
    ocr_urls: tuple[str, ...]


def _env_bool(name: str, default: bool) -> bool:
    """解析布尔环境变量。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    """解析正数环境变量，非法值回退默认值。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning(f"[ModelPreload] {name}={raw!r} 非法，回退默认 {default}")
        return default
    return max(value, 1.0)


def _split_urls(raw: str) -> tuple[str, ...]:
    """解析逗号分隔 URL 列表。"""

    return tuple(url.strip().rstrip("/") for url in raw.split(",") if url.strip())


def load_model_preload_config() -> ModelPreloadConfig:
    """从环境变量读取生产模型预热配置。"""

    urls_raw = os.getenv("OCR_SERVICE_URLS") or os.getenv("OCR_SERVICE_URL") or ""
    return ModelPreloadConfig(
        enabled=_env_bool("MODEL_PRELOAD_ENABLED", False),
        block_ready=_env_bool("MODEL_PRELOAD_BLOCK_READY", True),
        warmup_ocr=_env_bool("MODEL_PRELOAD_OCR", True),
        warmup_embedding=_env_bool("MODEL_PRELOAD_EMBEDDING", True),
        timeout_sec=_env_float("MODEL_PRELOAD_TIMEOUT_SEC", _DEFAULT_TIMEOUT_SEC),
        ocr_urls=_split_urls(urls_raw),
    )


def should_preload_production_models(worker_role: str) -> bool:
    """判断当前 worker 是否应执行生产模型预热。"""

    config = load_model_preload_config()
    return (
        config.enabled
        and settings.is_production()
        and worker_role == _PROCESS_WORKER_ROLE
    )


async def warmup_ocr_sidecars(config: ModelPreloadConfig) -> dict[str, Any]:
    """探测 OCR sidecar readiness，触发 sidecar 自身 health 初始化。"""

    if not config.ocr_urls:
        logger.info("[ModelPreload] 未配置 OCR_SERVICE_URL(S)，跳过 OCR 预热")
        return {"ok": True, "skipped": True, "urls": []}

    import httpx

    start = time.perf_counter()
    ready_urls: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(config.timeout_sec)) as client:
        for url in config.ocr_urls:
            resp = await client.get(f"{url}/health")
            resp.raise_for_status()
            data = resp.json()
            if data.get("ocr_ready") is not True:
                raise RuntimeError(f"OCR sidecar 未就绪: {url}")
            ready_urls.append(url)

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        f"[ModelPreload] OCR sidecar 预热完成: count={len(ready_urls)}, "
        f"elapsed_ms={elapsed_ms}"
    )
    return {"ok": True, "skipped": False, "urls": ready_urls, "elapsed_ms": elapsed_ms}


async def warmup_embedding_runtime(config: ModelPreloadConfig) -> dict[str, Any]:
    """通过真实 encode gate 路径预热 embedding 运行时。"""

    from app.core.vectorizer.encode_gate import safe_encode_batch

    device = (settings.EMBEDDING_DEVICE or "auto").lower()
    prefer_subprocess = device == "cpu"
    start = time.perf_counter()
    embeddings, meta = await asyncio.wait_for(
        safe_encode_batch(
            texts=[_PRELOAD_PROBE_TEXT],
            initial_batch_size=1,
            batch_no=1,
            batch_count=1,
            prefer_subprocess=prefer_subprocess,
        ),
        timeout=config.timeout_sec,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    shape = tuple(getattr(embeddings, "shape", ()))
    logger.info(
        f"[ModelPreload] embedding 预热完成: device={device}, "
        f"prefer_subprocess={prefer_subprocess}, shape={shape}, elapsed_ms={elapsed_ms}"
    )
    return {
        "ok": True,
        "device": device,
        "prefer_subprocess": prefer_subprocess,
        "shape": shape,
        "meta": meta,
        "elapsed_ms": elapsed_ms,
    }


async def warmup_production_models(worker_role: str) -> dict[str, Any]:
    """按生产 worker 角色执行模型预热。"""

    config = load_model_preload_config()
    if (
        not config.enabled
        or not settings.is_production()
        or worker_role != _PROCESS_WORKER_ROLE
    ):
        return {
            "ok": True,
            "skipped": True,
            "worker_role": worker_role,
            "production": settings.is_production(),
            "enabled": config.enabled,
        }

    logger.info(
        f"[ModelPreload] 开始生产模型预热: worker_role={worker_role}, "
        f"ocr={config.warmup_ocr}, embedding={config.warmup_embedding}, "
        f"block_ready={config.block_ready}, timeout={config.timeout_sec}s"
    )
    results: dict[str, Any] = {
        "ok": True,
        "skipped": False,
        "worker_role": worker_role,
        "errors": [],
    }

    for name, enabled, warmup in (
        ("ocr", config.warmup_ocr, warmup_ocr_sidecars),
        ("embedding", config.warmup_embedding, warmup_embedding_runtime),
    ):
        if not enabled:
            results[name] = {"ok": True, "skipped": True}
            continue
        try:
            results[name] = await warmup(config)
        except Exception as exc:
            results["ok"] = False
            results["errors"].append(str(exc))
            logger.warning(f"[ModelPreload] {name} 预热失败: {exc}")
            if config.block_ready:
                raise

    logger.info(f"[ModelPreload] 生产模型预热结束: ok={results['ok']}")
    return results
