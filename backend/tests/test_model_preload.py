"""生产模型预热单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import numpy as np
import pytest


def _enable_preload_env(monkeypatch, *, block_ready: bool = True) -> None:
    monkeypatch.setenv("MODEL_PRELOAD_ENABLED", "true")
    monkeypatch.setenv("MODEL_PRELOAD_BLOCK_READY", "true" if block_ready else "false")
    monkeypatch.setenv("MODEL_PRELOAD_OCR", "true")
    monkeypatch.setenv("MODEL_PRELOAD_EMBEDDING", "true")
    monkeypatch.setenv("MODEL_PRELOAD_TIMEOUT_SEC", "3")


@pytest.mark.asyncio
async def test_development_environment_skips_model_preload(monkeypatch):
    """本地/开发环境即使误配开关，也不得触发模型预热。"""
    from app.core.config import settings
    from app.core.runtime import model_preload

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    _enable_preload_env(monkeypatch)

    ocr = AsyncMock()
    embedding = AsyncMock()
    monkeypatch.setattr(model_preload, "warmup_ocr_sidecars", ocr)
    monkeypatch.setattr(model_preload, "warmup_embedding_runtime", embedding)

    result = await model_preload.warmup_production_models(worker_role="process")

    assert result["skipped"] is True
    ocr.assert_not_awaited()
    embedding.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_worker_in_production_warms_ocr_and_embedding(monkeypatch):
    """生产 Process Worker 应同时预热 OCR sidecar 与向量化模型。"""
    from app.core.config import settings
    from app.core.runtime import model_preload

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    _enable_preload_env(monkeypatch)

    ocr = AsyncMock(return_value={"ok": True, "urls": ["http://ocr:9000"]})
    embedding = AsyncMock(return_value={"ok": True, "device": "auto"})
    monkeypatch.setattr(model_preload, "warmup_ocr_sidecars", ocr)
    monkeypatch.setattr(model_preload, "warmup_embedding_runtime", embedding)

    result = await model_preload.warmup_production_models(worker_role="process")

    assert result["ok"] is True
    ocr.assert_awaited_once()
    embedding.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_process_worker_skips_model_preload(monkeypatch):
    """LLM / maintenance 等非 process worker 不应默认预热模型。"""
    from app.core.config import settings
    from app.core.runtime import model_preload

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    _enable_preload_env(monkeypatch)

    ocr = AsyncMock()
    embedding = AsyncMock()
    monkeypatch.setattr(model_preload, "warmup_ocr_sidecars", ocr)
    monkeypatch.setattr(model_preload, "warmup_embedding_runtime", embedding)

    result = await model_preload.warmup_production_models(worker_role="llm")

    assert result["skipped"] is True
    ocr.assert_not_awaited()
    embedding.assert_not_awaited()


@pytest.mark.asyncio
async def test_block_ready_true_raises_when_warmup_fails(monkeypatch):
    """阻塞 ready 模式下预热失败必须抛出，避免未预热 worker 写 alive。"""
    from app.core.config import settings
    from app.core.runtime import model_preload

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    _enable_preload_env(monkeypatch, block_ready=True)

    monkeypatch.setattr(
        model_preload,
        "warmup_ocr_sidecars",
        AsyncMock(side_effect=RuntimeError("ocr down")),
    )
    monkeypatch.setattr(
        model_preload,
        "warmup_embedding_runtime",
        AsyncMock(return_value={"ok": True}),
    )

    with pytest.raises(RuntimeError, match="ocr down"):
        await model_preload.warmup_production_models(worker_role="process")


@pytest.mark.asyncio
async def test_block_ready_false_records_failure_and_continues(monkeypatch):
    """非阻塞模式下预热失败只记录结果，不阻断 worker 启动。"""
    from app.core.config import settings
    from app.core.runtime import model_preload

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    _enable_preload_env(monkeypatch, block_ready=False)

    monkeypatch.setattr(
        model_preload,
        "warmup_ocr_sidecars",
        AsyncMock(side_effect=RuntimeError("ocr down")),
    )
    monkeypatch.setattr(
        model_preload,
        "warmup_embedding_runtime",
        AsyncMock(return_value={"ok": True}),
    )

    result = await model_preload.warmup_production_models(worker_role="process")

    assert result["ok"] is False
    assert result["errors"] == ["ocr down"]


@pytest.mark.asyncio
async def test_cpu_embedding_warmup_uses_subprocess_path(monkeypatch):
    """CPU 部署下预热必须覆盖 encode_worker 子进程缓存路径。"""
    from app.core.config import settings
    from app.core.runtime import model_preload

    monkeypatch.setattr(settings, "EMBEDDING_DEVICE", "cpu")
    _enable_preload_env(monkeypatch)
    config = model_preload.load_model_preload_config()

    encode = AsyncMock(
        return_value=(
            np.zeros((1, 1024), dtype=np.float32),
            {"device": "cpu", "recovery_mode": "process"},
        )
    )
    monkeypatch.setattr("app.core.vectorizer.encode_gate.safe_encode_batch", encode)

    result = await model_preload.warmup_embedding_runtime(config)

    assert result["ok"] is True
    assert result["device"] == "cpu"
    kwargs = encode.await_args.kwargs
    assert kwargs["texts"] == ["模型预热"]
    assert kwargs["initial_batch_size"] == 1
    assert kwargs["prefer_subprocess"] is True
