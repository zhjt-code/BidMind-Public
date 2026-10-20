"""子进程 encode 入口 —— 供 ProcessPoolExecutor 使用。

职责: 在可销毁子进程中执行向量化, 并在子进程生命周期内复用同一 EmbeddingModel。
不做: 不跨进程共享模型 (各子进程独立缓存); 不负责超时/销毁 (由
 encode_gate._get_encode_executor 管理)。

每个子进程独立加载 EmbeddingModel，encode 完毕后将结果序列化为
Python list[list[float]] 返回给主进程（ndarray 跨进程 pickle 开销大）。: 子进程内按 device 做模块级缓存。ProcessPoolExecutor 的子进程会被
复用处理多个 encode 任务, 若每次调用都重建 EmbeddingModel, 将反复加载
~600MB 模型 (单次 ~3.7s), 在 BID 提取等"逐条子查询 encode"的热路径上造成
雪崩式慢化。缓存后, 模型在一个子进程生命周期内只加载一次; 超时销毁子进程时
缓存随进程一并释放, 进程本身仍是缓存边界。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.vectorizer.embedding import EmbeddingModel


# 子进程内按 device 缓存 EmbeddingModel 实例
_model_cache: dict[str, "EmbeddingModel"] = {}
_model_cache_lock = threading.Lock()


def _get_or_create_model(device: str) -> "EmbeddingModel":
    """获取本子进程内按 device 缓存的 EmbeddingModel, 首次调用时构造。"""
    model = _model_cache.get(device)
    if model is None:
        with _model_cache_lock:
            model = _model_cache.get(device)
            if model is None:
                from app.core.vectorizer.embedding import EmbeddingModel

                model = EmbeddingModel(device=device)
                _model_cache[device] = model
    return model


def encode_texts_in_process(
    texts: list[str],
    batch_size: int,
    device: str,
) -> list[list[float]]:
    """在独立子进程中执行向量化。

    Args:
        texts: 待向量化的文本列表
        batch_size: encode batch_size
        device: "cpu" / "cuda" / "auto"

    Returns:
        list[list[float]]: 二维 list，形状 (len(texts), 1024)
    """
    model = _get_or_create_model(device)
    embeddings = model.encode(texts, batch_size=batch_size)
    return embeddings.tolist()
