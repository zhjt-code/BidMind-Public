""" 防御测试: ONNX 后端选择策略 + 进程级损坏记忆 + 子进程模型缓存。

映射真实回归:
- df953b8 引入的 auto→ONNX(CPU) 策略, 在 bge-m3 无有效 ONNX 导出时每次支付
 "试 ONNX 失败 ~1.9s -> 回退 PyTorch" 的无效开销。
- encode_worker 子进程每次调用重建 EmbeddingModel, 在逐条子查询 encode 热路径
 造成 ~3.7s/条 的模型重载雪崩。

断言关注真实输出(选了哪个后端 / 模型构造几次), 而非"能跑通"。
"""

import sys
import types

import numpy as np
import pytest

from app.core.vectorizer import embedding as embedding_module
from app.core.vectorizer.embedding import EmbeddingModel


@pytest.fixture(autouse=True)
def _reset_onnx_memo():
    """每个用例前后复位进程级 ONNX 损坏记忆, 避免用例间串扰。"""
    embedding_module._onnx_runtime_broken = False
    yield
    embedding_module._onnx_runtime_broken = False


@pytest.fixture
def _restore_backend_cfg():
    old = embedding_module.settings.EMBEDDING_BACKEND
    yield
    embedding_module.settings.EMBEDDING_BACKEND = old


# ── _resolve_backend 策略 ────────────────────────────────────────────────

def test_auto_backend_cpu_does_not_select_onnx(monkeypatch, _restore_backend_cfg):
    """auto 模式即便 CPU + onnxruntime 可用, 也不再选择 ONNX (核心)。"""
    embedding_module.settings.EMBEDDING_BACKEND = "auto"
    monkeypatch.setattr(EmbeddingModel, "_onnx_available", staticmethod(lambda: True))

    model = EmbeddingModel(device="cpu")

    assert model._backend is None, "auto 模式 CPU 不应再选 ONNX"


def test_explicit_onnx_selected_when_available(monkeypatch, _restore_backend_cfg):
    """显式 onnx + onnxruntime 可用 + 未损坏 → 选 ONNX。"""
    embedding_module.settings.EMBEDDING_BACKEND = "onnx"
    monkeypatch.setattr(EmbeddingModel, "_onnx_available", staticmethod(lambda: True))

    model = EmbeddingModel(device="cpu")

    assert model._backend == "onnx"


def test_explicit_onnx_skipped_after_runtime_broken(monkeypatch, _restore_backend_cfg):
    """显式 onnx 但本进程已记忆 ONNX 损坏 → 回退 PyTorch, 不再尝试。"""
    embedding_module.settings.EMBEDDING_BACKEND = "onnx"
    monkeypatch.setattr(EmbeddingModel, "_onnx_available", staticmethod(lambda: True))
    embedding_module._mark_onnx_runtime_broken()

    model = EmbeddingModel(device="cpu")

    assert model._backend is None, "损坏记忆后不应再选 ONNX"


def test_load_onnx_failure_marks_runtime_broken(monkeypatch, _restore_backend_cfg):
    """ONNX 加载/验证失败时应记忆损坏, 并回退到 PyTorch 模型。"""
    embedding_module.settings.EMBEDDING_BACKEND = "onnx"
    monkeypatch.setattr(EmbeddingModel, "_onnx_available", staticmethod(lambda: True))

    model = EmbeddingModel(device="cpu")
    assert model._backend == "onnx"

    # 模拟 ONNX 构造/探测抛出 'last_hidden_state' 同类运行时错误
    def _raise_onnx(*_args, **_kwargs):
        raise KeyError("last_hidden_state")

    monkeypatch.setattr(embedding_module, "SentenceTransformer", _raise_onnx)

    sentinel = object()
    monkeypatch.setattr(model, "_load_pytorch_model", lambda: sentinel)

    result = model._load_onnx_model()

    assert result is sentinel, "失败应回退到 PyTorch 模型"
    assert model._backend is None
    assert embedding_module._is_onnx_runtime_broken() is True, "应记忆 ONNX 损坏"


# ── encode_worker 子进程模型缓存 ─────────────────────────────────────────

def test_encode_worker_caches_model_per_device(monkeypatch):
    """子进程 encode 多次调用应复用同一 EmbeddingModel, 只构造一次。"""
    from app.core.vectorizer import encode_worker

    encode_worker._model_cache.clear()

    construct_count = {"n": 0}

    class _FakeModel:
        def __init__(self, device=None, **_kwargs):
            construct_count["n"] += 1
            self.device = device

        def encode(self, texts, batch_size=32, **_kwargs):
            return np.zeros((len(texts), 4), dtype=np.float32)

    fake_mod = types.ModuleType("app.core.vectorizer.embedding")
    fake_mod.EmbeddingModel = _FakeModel
    monkeypatch.setitem(sys.modules, "app.core.vectorizer.embedding", fake_mod)

    out1 = encode_worker.encode_texts_in_process(["a", "b"], batch_size=2, device="cpu")
    out2 = encode_worker.encode_texts_in_process(["c"], batch_size=1, device="cpu")

    assert construct_count["n"] == 1, "同一 device 下模型只应构造一次"
    assert len(out1) == 2 and len(out2) == 1
    assert isinstance(out1, list) and isinstance(out1[0], list)

    encode_worker._model_cache.clear()


def test_encode_worker_caches_per_device_separately(monkeypatch):
    """不同 device 各自缓存独立实例。"""
    from app.core.vectorizer import encode_worker

    encode_worker._model_cache.clear()
    construct_count = {"n": 0}

    class _FakeModel:
        def __init__(self, device=None, **_kwargs):
            construct_count["n"] += 1
            self.device = device

        def encode(self, texts, batch_size=32, **_kwargs):
            return np.zeros((len(texts), 4), dtype=np.float32)

    fake_mod = types.ModuleType("app.core.vectorizer.embedding")
    fake_mod.EmbeddingModel = _FakeModel
    monkeypatch.setitem(sys.modules, "app.core.vectorizer.embedding", fake_mod)

    encode_worker.encode_texts_in_process(["a"], batch_size=1, device="cpu")
    encode_worker.encode_texts_in_process(["b"], batch_size=1, device="cuda")
    encode_worker.encode_texts_in_process(["c"], batch_size=1, device="cpu")

    assert construct_count["n"] == 2, "cpu / cuda 各构造一次, 共两次"

    encode_worker._model_cache.clear()
