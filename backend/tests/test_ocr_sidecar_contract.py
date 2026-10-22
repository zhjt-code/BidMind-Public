"""OCR sidecar HTTP contract tests."""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _load_sidecar_module():
    """Load ocr/main.py without requiring ocr to be a package."""

    module_path = Path(__file__).resolve().parents[2] / "ocr" / "main.py"
    spec = importlib.util.spec_from_file_location("ocr_sidecar_main", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tiny_png_b64() -> str:
    return base64.b64encode(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
            "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
    ).decode("ascii")


def test_predict_returns_503_when_sidecar_inference_crashes(monkeypatch) -> None:
    """GPU/Paddle inference errors must be visible to the backend pool health logic."""

    sidecar = _load_sidecar_module()

    def _crash(_image_array):
        raise RuntimeError("cuda error")

    monkeypatch.setattr(sidecar._engine, "predict", _crash)
    client = TestClient(sidecar.app)

    response = client.post("/predict", json={"image_b64": _tiny_png_b64()})

    assert response.status_code == 503


def test_predict_returns_503_when_sidecar_is_not_initialized(monkeypatch) -> None:
    """Initialization failure is service degradation, not a valid empty OCR result."""

    sidecar = _load_sidecar_module()
    monkeypatch.setattr(sidecar._engine, "predict", lambda _image_array: {})
    monkeypatch.setattr(sidecar._engine, "ensure_initialized", lambda: False)
    client = TestClient(sidecar.app)

    response = client.post("/predict", json={"image_b64": _tiny_png_b64()})

    assert response.status_code == 503
