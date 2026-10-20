"""连续原生失败达到阈值才退出进程。阈值 0 关闭该行为。"""

from __future__ import annotations

import base64
import importlib.util
import io
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

_MAIN = Path(__file__).resolve().parents[2].parent / "ocr" / "main.py"


def _png() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _Engine:
    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.pages_ok = 0
        self.failing = True

    def ensure_initialized(self) -> bool:
        return True

    def stats(self) -> str:
        return f"consecutive={self.consecutive_failures}"

    def predict(self, _image: object) -> list:
        if self.failing:
            self.consecutive_failures += 1
            raise RuntimeError("std::exception")
        self.consecutive_failures = 0
        self.pages_ok += 1
        return []


def _load(name: str, threshold: str):
    os.environ["OCR_PIPELINE"] = "ocr"
    os.environ["STRUCTURE_MAX_CONSECUTIVE_FAILURES"] = threshold
    spec = importlib.util.spec_from_file_location(name, _MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    engine = _Engine()
    module._structure_engine = engine
    exits: list[int] = []
    module._exit_for_restart = lambda: exits.append(1)
    return module, engine, exits


def _post(module, payload: dict) -> int:
    client = TestClient(module.app)
    return client.post("/structure", json=payload).status_code


def test_third_consecutive_failure_exits() -> None:
    module, _engine, exits = _load("structure_sidecar_exit", "3")
    payload = {"image_b64": _png()}
    assert [_post(module, payload) for _ in range(3)] == [503, 503, 503]
    assert exits == [1]


def test_success_resets_streak_before_exit() -> None:
    module, engine, exits = _load("structure_sidecar_reset", "3")
    payload = {"image_b64": _png()}
    assert _post(module, payload) == 503
    assert _post(module, payload) == 503
    engine.failing = False
    assert _post(module, payload) == 200
    engine.failing = True
    assert _post(module, payload) == 503
    assert exits == []


def test_zero_threshold_never_exits() -> None:
    module, _engine, exits = _load("structure_sidecar_disabled", "0")
    payload = {"image_b64": _png()}
    assert [_post(module, payload) for _ in range(4)] == [503, 503, 503, 503]
    assert exits == []
