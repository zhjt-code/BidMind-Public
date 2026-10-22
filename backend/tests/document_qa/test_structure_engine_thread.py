"""版面模型的创建和推理必须落在同一个专用线程上。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import ModuleType

import numpy as np

_OCR = Path(__file__).resolve().parents[2].parent / "ocr"
if str(_OCR) not in sys.path:
    sys.path.insert(0, str(_OCR))

_calls: list[tuple[str, str]] = []


class _FakePipeline:
    def __init__(self, **_kwargs: object) -> None:
        _calls.append(("init", threading.current_thread().name))

    def predict(self, _image: np.ndarray) -> list[dict]:
        _calls.append(("predict", threading.current_thread().name))
        if getattr(self, "fail", False):
            raise RuntimeError("std::exception")
        return [{"res": {"parsing_res_list": [], "boxes": []}}]


class _FakeLayout(_FakePipeline):
    pass


def _install_fake_paddleocr() -> None:
    module = ModuleType("paddleocr")
    module.PPStructureV3 = _FakePipeline  # type: ignore[attr-defined]
    module.LayoutDetection = _FakeLayout  # type: ignore[attr-defined]
    sys.modules["paddleocr"] = module


def test_default_mode_uses_layout_detection(monkeypatch) -> None:
    monkeypatch.delenv("STRUCTURE_ENGINE", raising=False)
    _install_fake_paddleocr()
    from structure_engine import StructureEngine

    engine = StructureEngine()
    assert engine.ensure_initialized()
    assert engine.mode == "layout"
    assert type(engine._pipeline) is _FakeLayout


def test_ppstructure_mode_is_the_rollback_switch(monkeypatch) -> None:
    monkeypatch.setenv("STRUCTURE_ENGINE", "ppstructure")
    _install_fake_paddleocr()
    from structure_engine import StructureEngine

    engine = StructureEngine()
    assert engine.ensure_initialized()
    assert engine.mode == "ppstructure"
    assert type(engine._pipeline) is _FakePipeline


def test_init_and_predict_share_one_worker_thread() -> None:
    _calls.clear()
    _install_fake_paddleocr()
    from structure_engine import StructureEngine

    engine = StructureEngine()
    callers: list[str] = []

    def _init() -> None:
        callers.append(threading.current_thread().name)
        assert engine.ensure_initialized()

    starter = threading.Thread(target=_init, name="caller-init")
    starter.start()
    starter.join()

    errors: list[BaseException] = []

    def _predict() -> None:
        callers.append(threading.current_thread().name)
        try:
            assert engine.predict(np.zeros((4, 4, 3), dtype=np.uint8)) == []
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [threading.Thread(target=_predict, name=f"caller-{index}") for index in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert errors == []
    threads = {name for _kind, name in _calls}
    assert len(threads) == 1
    infer_thread = threads.pop()
    assert infer_thread.startswith("structure-infer")
    assert infer_thread not in callers
    assert engine.infer_thread == infer_thread
    assert "infer_thread=" in engine.stats()


def test_failure_count_resets_after_success() -> None:
    _calls.clear()
    _install_fake_paddleocr()
    from structure_engine import StructureEngine

    engine = StructureEngine()
    assert engine.ensure_initialized()
    engine._pipeline.fail = True
    try:
        engine.predict(np.zeros((2, 2, 3), dtype=np.uint8))
    except RuntimeError:
        pass
    assert (engine.failures, engine.consecutive_failures, engine.pages_ok) == (1, 1, 0)
    engine._pipeline.fail = False
    assert engine.predict(np.zeros((2, 2, 3), dtype=np.uint8)) == []
    assert (engine.failures, engine.consecutive_failures, engine.pages_ok) == (1, 0, 1)
