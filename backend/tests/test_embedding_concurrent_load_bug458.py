""" 防御测试: EmbeddingModel.model 懒加载并发安全。

映射真实回归:
- 审查高峰 GPU stall 后, 多个 encode 经 cpu_encode_semaphore(允许并发) 并发首次
 访问同一 CPU 兜底单例的 `.model`, 并发构造 SentenceTransformer 触发 transformers
 (meta device 初始化权重) 的全局 torch 状态竞态, 报 `Cannot copy out of meta tensor`,
 加载失败后 _model 恒为 None 反复重载 (日志刷屏 `正在加载向量化模型`)。

断言关注真实输出(并发首次加载只构造一次 / 失败后可重试不死锁), 而非"能跑通"。
"""

import threading
import time

import pytest

from app.core.vectorizer.embedding import EmbeddingModel


def _make_cpu_model() -> EmbeddingModel:
    """构造一个走 PyTorch 分支的 CPU EmbeddingModel(不触发真实加载)。"""
    model = EmbeddingModel(device="cpu")
    model._backend = None  # 强制走 _load_pytorch_model 分支
    return model


def test_model_property_concurrent_load_constructs_once(monkeypatch):
    """N 线程并发首次访问 .model, 模型只应被构造一次 (锁 + 双检生效)。

    无锁时多个线程会同时进入加载段并发构造模型, 触发 meta tensor 竞态;
    本用例用 sleep 扩大竞态窗口, 断言构造次数恒为 1。
    """
    model = _make_cpu_model()

    construct_count = {"n": 0}
    count_lock = threading.Lock()
    sentinel = object()

    def _fake_load():
        with count_lock:
            construct_count["n"] += 1
        time.sleep(0.05)  # 扩大竞态窗口: 无锁则其余线程会同时进入
        return sentinel

    monkeypatch.setattr(model, "_load_pytorch_model", _fake_load)
    monkeypatch.setattr(model, "_apply_cpu_budget_to_model", lambda _m: None)

    n = 8
    barrier = threading.Barrier(n)
    seen: list[object] = []
    seen_lock = threading.Lock()
    errors: list[BaseException] = []

    def worker():
        barrier.wait()
        try:
            m = model.model
            with seen_lock:
                seen.append(m)
        except BaseException as exc:  # noqa: BLE001
            with seen_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发加载不应抛异常, 实际: {errors}"
    assert construct_count["n"] == 1, (
        f"并发首次加载模型只应构造一次, 实际 {construct_count['n']} 次"
    )
    assert len(seen) == n and all(m is sentinel for m in seen), (
        "所有线程应拿到同一个已加载模型实例"
    )


def test_model_property_load_failure_allows_retry(monkeypatch):
    """加载失败时锁须正确释放, _model 保持 None 且下次访问可重试(不死锁)。"""
    model = _make_cpu_model()
    monkeypatch.setattr(model, "_apply_cpu_budget_to_model", lambda _m: None)

    calls = {"n": 0}
    sentinel = object()

    def _flaky_load():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("模拟首次加载失败")
        return sentinel

    monkeypatch.setattr(model, "_load_pytorch_model", _flaky_load)

    with pytest.raises(RuntimeError, match="模拟首次加载失败"):
        _ = model.model
    assert model._model is None, "加载失败后 _model 不应被赋值"

    # 第二次访问应能正常重试并成功(锁未死锁)
    assert model.model is sentinel
    assert calls["n"] == 2
