"""文本向量化模块

使用sentence-transformers和BAAI/bge-m3模型将文本转换为向量表示。
"""

import threading
import time
from typing import Optional, Union

import numpy as np
from loguru import logger
from sentence_transformers import SentenceTransformer

from app.core.config import settings

# 进程级 ONNX 损坏记忆 
# 职责: 记录"本进程内 ONNX 后端已被验证为损坏", 避免每个新建的 EmbeddingModel
#       (尤其是 encode 子进程/CPU 回退路径) 反复支付 ~1.9s 的"加载 ONNX -> 探测失败
#       -> 回退 PyTorch"无效开销。
# 不做: 不跨进程传播 (子进程各自独立记忆); 不替代 EMBEDDING_BACKEND 配置 (显式
#       pytorch 永不尝试 ONNX)。
_onnx_runtime_broken: bool = False
_onnx_runtime_broken_lock = threading.Lock()


# 进程级模型加载锁 
# 职责: 串行化本进程内所有 EmbeddingModel 实例的 SentenceTransformer 构造,
#       杜绝并发首次加载时 transformers(meta device 初始化权重) 的全局 torch
#       状态竞态 (现象: `Cannot copy out of meta tensor`)。
# 不做: 不缓存模型 (各实例仍各自持有 _model); 不替代单例 (单例减少实例数,
#       本锁只保证"任意时刻至多一个模型在构造")。
_model_load_lock = threading.Lock()


def _mark_onnx_runtime_broken() -> None:
    """标记本进程 ONNX 后端损坏, 后续 _resolve_backend 不再选择 ONNX。"""
    global _onnx_runtime_broken
    with _onnx_runtime_broken_lock:
        _onnx_runtime_broken = True


def _is_onnx_runtime_broken() -> bool:
    """读取本进程 ONNX 损坏记忆 (无锁读, bool 读取受 GIL 保护原子)。"""
    return _onnx_runtime_broken


def _log_cuda_unavailable(configured: str) -> None:
    """CUDA 不可用时按"是否 CPU-only 构建"分级输出诊断日志。

 职责: 区分"装错 torch 包"与"环境本就无 GPU", 让日志直接指向排查方向。
 不做: 不修改设备决策, 不尝试修复安装。

 CPU-only 轮 (Windows 上 PyPI 的默认 torch) 的 ``torch.version.cuda`` 为
 None, 此时 CUDA 不可用与驱动无关, 必须升级为 WARNING 并点名 torch 版本;
 否则只看到"未检测到CUDA"会把排查方向误导到驱动侧。
 """
    import torch

    version = getattr(torch, "__version__", "unknown")
    cpu_only_build = getattr(torch.version, "cuda", None) is None

    if cpu_only_build:
        logger.warning(
            f"配置 EMBEDDING_DEVICE={configured} 但未检测到CUDA，回退CPU: "
            f"当前 torch={version} 为 CPU-only 构建 (torch.version.cuda=None)，"
            f"与驱动无关；需从 CUDA 专用索引重装 torch 才能启用 GPU"
        )
    elif configured == "cuda":
        logger.warning(
            f"配置了CUDA但未检测到GPU，回退到CPU: torch={version} 含 CUDA 支持，"
            f"请检查驱动与显卡可见性"
        )
    else:
        logger.info(f"未检测到CUDA，使用CPU: torch={version}")


class EmbeddingModel:
    """文本向量化模型
    
    基于sentence-transformers加载bge-m3模型，
    将文本转换为1024维向量表示。
    
    示例:
        >>> model = EmbeddingModel()
        >>> embeddings = model.encode(["这是一段测试文本"])
        >>> print(embeddings.shape)  # (1, 1024)
    """
    
    # 模型输出维度
    EMBEDDING_DIM = 1024
    
    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        normalize: bool = True
    ):
        """初始化向量化模型
        
        Args:
            model_name: 模型名称或路径，默认使用配置中的本地模型
            device: 运行设备（cpu/cuda/auto），默认使用配置中的EMBEDDING_DEVICE
                    设为 "auto" 时自动选择：有GPU用GPU，否则用CPU
            normalize: 是否对输出向量进行L2归一化，默认True
        """
        self.model_name = model_name or settings.get_embedding_model_path()
        self.normalize = normalize
        self._model: Optional[SentenceTransformer] = None
        
        configured_device = device or settings.EMBEDDING_DEVICE
        self.device = self._resolve_device(configured_device)
        
        self._backend: Optional[str] = self._resolve_backend()
        
        logger.info(
            f"EmbeddingModel初始化配置: model={self.model_name}, "
            f"device={self.device}, backend={self._backend or 'pytorch'}, "
            f"normalize={self.normalize}"
        )
    
    def _resolve_device(self, device: str) -> str:
        """解析设备配置
        
        Args:
            device: 配置的设备（cpu/cuda/auto）
            
        Returns:
            str: 实际使用的设备
        """
        import torch
        
        normalized = device.lower()
        
        if normalized == "auto":
            if torch.cuda.is_available():
                logger.info("自动检测到CUDA可用，使用GPU加速")
                return "cuda"
            _log_cuda_unavailable("auto")
            return "cpu"
        
        # 验证cuda配置
        if normalized == "cuda" and not torch.cuda.is_available():
            _log_cuda_unavailable("cuda")
            return "cpu"
        
        return device
    
    @staticmethod
    def _onnx_available() -> bool:
        """检测 onnxruntime 是否可用"""
        try:
            import onnxruntime  # noqa: F401
            return True
        except ImportError:
            return False
    
    def _resolve_backend(self) -> Optional[str]:
        """根据 EMBEDDING_BACKEND 配置和运行环境决定推理后端

 Returns:
 "onnx" 表示使用 ONNX Runtime, None 表示使用 PyTorch

 策略 (修订):
 - ``pytorch``: 永远 PyTorch。
 - ``onnx``: 显式要求 ONNX, 仅在 onnxruntime 可用且本进程未记忆 ONNX 损坏时选择。
 - ``auto``: **不再在 CPU 上盲目选择 ONNX**。bge-m3 在本项目未交付有效 ONNX 导出,
 auto 选 ONNX 只会每次支付"加载->探测失败->回退 PyTorch"的无效开销 (零收益)。
 因此 auto 一律 PyTorch; 需要 ONNX 加速时由部署层显式置 EMBEDDING_BACKEND=onnx。
 """
        cfg = settings.EMBEDDING_BACKEND
        if cfg == "onnx":
            if not self._onnx_available():
                logger.warning("EMBEDDING_BACKEND=onnx 但 onnxruntime 未安装, 回退PyTorch")
                return None
            if _is_onnx_runtime_broken():
                logger.warning("EMBEDDING_BACKEND=onnx 但本进程已记忆 ONNX 损坏, 回退PyTorch")
                return None
            return "onnx"
        # pytorch / auto 均使用 PyTorch
        return None
    
    @property
    def model(self) -> SentenceTransformer:
        """延迟加载模型
        
        CPU 环境优先尝试 ONNX 后端; 失败则回退 PyTorch。
        GPU 环境直接使用 PyTorch。

        线程预算:
        - CPU 路径会注入 cpu_budget 计算出的 intra_op/inter_op_num_threads,
          避免 ONNX/PyTorch 默认按全部逻辑核展开导致与 OCR/解析争核
        - GPU 路径不受 cpu_budget 影响
        """
        # 双重检查 + 进程级加载锁。并发首次访问时仅一个线程构造模型,
        # 其余线程进锁后命中已加载实例直接返回, 避免并发构造触发 meta tensor 竞态。
        if self._model is None:
            with _model_load_lock:
                if self._model is None:
                    logger.info(f"正在加载向量化模型: {self.model_name}")
                    if self._backend == "onnx":
                        loaded = self._load_onnx_model()
                    else:
                        loaded = self._load_pytorch_model()
                    self._apply_cpu_budget_to_model(loaded)
                    self._model = loaded
        return self._model

    def _load_onnx_model(self) -> SentenceTransformer:
        """以 ONNX 后端加载模型, 失败回退 PyTorch。"""
        from app.core.runtime.cpu_budget import build_onnx_session_options

        try:
            sess_options = build_onnx_session_options()
            model_kwargs: dict = {"provider": "CPUExecutionProvider"}
            if sess_options is not None:
                model_kwargs["session_options"] = sess_options
            model = SentenceTransformer(
                self.model_name,
                backend="onnx",
                model_kwargs=model_kwargs,
            )
            model.encode(["probe"], normalize_embeddings=True)
            logger.info(
                f"ONNX模型加载并验证完成: {self.model_name}, "
                f"session_options={'custom' if sess_options is not None else 'default'}"
            )
            return model
        except Exception as e:
            logger.warning(f"ONNX加载/验证失败, 回退PyTorch: {e}")
            self._backend = None
            # 记忆本进程 ONNX 损坏, 避免后续实例反复支付无效尝试开销
            _mark_onnx_runtime_broken()
            return self._load_pytorch_model()

    def _load_pytorch_model(self) -> SentenceTransformer:
        """以 PyTorch 后端加载模型; CPU 设备额外应用 torch 线程上限。"""
        if self.device == "cpu":
            from app.core.runtime.cpu_budget import apply_torch_thread_limits

            apply_torch_thread_limits()
        model = SentenceTransformer(self.model_name, device=self.device)
        logger.info(f"PyTorch模型加载完成: {self.model_name}, device={self.device}")
        return model

    def _apply_cpu_budget_to_model(self, model: SentenceTransformer) -> None:
        """对 CPU 路径(ONNX 或 CPU PyTorch)收紧 max_seq_length, 减少 padding 浪费。

        bge-m3 默认 max_seq_length=8192, 但 indexer 已通过 VECTOR_MAX_ITEM_CHARS=1800
        约束输入文本长度, 业务真实文本中位数远低于 512 token, 因此把 max_seq_length
        固定到 settings.VECTOR_CPU_MAX_SEQ_LENGTH (默认 512) 不会截断业务内容,
        但能让 padding 区显著缩小, CPU 推理收益最直接。

        GPU 路径不收紧——GPU 显存能装下全长 padding, 收紧反而会因边界文本截断带来精度风险。
        """
        if self._backend != "onnx" and self.device != "cpu":
            return
        try:
            from app.core.config import settings

            new_max = int(getattr(settings, "VECTOR_CPU_MAX_SEQ_LENGTH", 512))
            if new_max <= 0:
                return
            current = getattr(model, "max_seq_length", None)
            if current is not None and int(current) <= new_max:
                return
            model.max_seq_length = new_max
            logger.info(
                f"CPU 路径已收紧 max_seq_length: {current} -> {new_max}"
            )
        except Exception as exc:
            logger.debug(f"应用 CPU max_seq_length 失败(忽略): {exc}")
    
    def encode(
        self,
        texts: Union[str, list[str]],
        batch_size: int = 32,
        show_progress: bool = False,
        progress_callback: Optional[callable] = None,
    ) -> np.ndarray:
        """将文本转换为向量

        Args:
            texts: 单个文本或文本列表
            batch_size: 批处理大小，默认32
            show_progress: 是否显示进度条，默认False
            progress_callback: 每完成一个batch后的回调,签名 callback(done, total)

        Returns:
            np.ndarray: 向量数组，形状为 (n, EMBEDDING_DIM)
        """
        if isinstance(texts, str):
            texts = [texts]

        if not texts:
            logger.warning("输入文本列表为空")
            return np.array([]).reshape(0, self.EMBEDDING_DIM)

        total = len(texts)
        logger.debug(f"开始向量化: {total} 条文本, batch_size={batch_size}")
        text_lengths = np.array([len(t) for t in texts], dtype=np.int32)
        approx_total_tokens = int(max(1, text_lengths.sum() // 2))

        if progress_callback is None:
            start_time = time.perf_counter()
            embeddings = self.model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=show_progress,
                normalize_embeddings=self.normalize,
                convert_to_numpy=True,
            )
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            # 单 query 编码 (检索/提取等热路径) 降为 DEBUG, 避免每个子
            # 查询都打 INFO 噪音; 批量 (total > 1) 仍保留 INFO 供链路观测。
            log_fn = logger.debug if total <= 1 else logger.info
            log_fn(
                f"向量化批处理完成: total={total}, batch_size={batch_size}, elapsed_ms={elapsed_ms}, "
                f"char_len[min/p50/p95/max]="
                f"{int(text_lengths.min()) if total else 0}/"
                f"{int(np.percentile(text_lengths, 50)) if total else 0}/"
                f"{int(np.percentile(text_lengths, 95)) if total else 0}/"
                f"{int(text_lengths.max()) if total else 0}, "
                f"approx_tokens={approx_total_tokens}"
            )
        else:
            # 手动分batch以支持逐batch进度回调
            all_embeddings: list[np.ndarray] = []
            done = 0
            cumulative_tokens = 0
            for i in range(0, total, batch_size):
                batch_texts = texts[i : i + batch_size]
                batch_lengths = np.array([len(t) for t in batch_texts], dtype=np.int32)
                batch_tokens = int(max(1, batch_lengths.sum() // 2))
                batch_start = time.perf_counter()
                batch_emb = self.model.encode(
                    batch_texts,
                    batch_size=batch_size,
                    show_progress_bar=False,
                    normalize_embeddings=self.normalize,
                    convert_to_numpy=True,
                )
                batch_elapsed_ms = int((time.perf_counter() - batch_start) * 1000)
                all_embeddings.append(batch_emb)
                prev_done = done
                done += len(batch_texts)
                cumulative_tokens += batch_tokens
                logger.info(
                    f"向量化批次完成: batch={i}-{i + len(batch_texts) - 1}, "
                    f"batch_size={len(batch_texts)}, elapsed_ms={batch_elapsed_ms}, "
                    f"char_len[min/p50/p95/max]="
                    f"{int(batch_lengths.min()) if len(batch_lengths) else 0}/"
                    f"{int(np.percentile(batch_lengths, 50)) if len(batch_lengths) else 0}/"
                    f"{int(np.percentile(batch_lengths, 95)) if len(batch_lengths) else 0}/"
                    f"{int(batch_lengths.max()) if len(batch_lengths) else 0}, "
                    f"approx_tokens={batch_tokens}, cumulative_tokens={cumulative_tokens}"
                )
                if total > 0 and (done * 10 // total) > (prev_done * 10 // total):
                    logger.info(f"向量化进度: {done}/{total}")
                try:
                    progress_callback(done, total)
                except Exception:
                    pass
            embeddings = np.vstack(all_embeddings)

        logger.debug(f"向量化完成: 输出形状 {embeddings.shape}")

        return embeddings
    
    def encode_single(self, text: str) -> np.ndarray:
        """向量化单个文本
        
        Args:
            text: 输入文本
            
        Returns:
            np.ndarray: 一维向量，形状为 (768,)
        """
        if not text or not text.strip():
            logger.warning("输入文本为空")
            return np.zeros(self.EMBEDDING_DIM)
        
        embedding = self.encode([text])[0]
        return embedding
    
    def similarity(
        self,
        text1: str,
        text2: str
    ) -> float:
        """计算两个文本的余弦相似度
        
        Args:
            text1: 第一个文本
            text2: 第二个文本
            
        Returns:
            float: 余弦相似度，范围 [-1, 1]
        """
        emb1 = self.encode_single(text1)
        emb2 = self.encode_single(text2)
        
        # 由于已经L2归一化，余弦相似度等于点积
        return float(np.dot(emb1, emb2))
    
    def batch_similarity(
        self,
        query: str,
        candidates: list[str]
    ) -> list[float]:
        """计算查询文本与候选文本列表的相似度
        
        Args:
            query: 查询文本
            candidates: 候选文本列表
            
        Returns:
            list[float]: 相似度列表
        """
        if not candidates:
            return []
        
        query_emb = self.encode_single(query)
        candidate_embs = self.encode(candidates)
        
        # 批量计算点积
        similarities = np.dot(candidate_embs, query_emb)
        
        return similarities.tolist()


# 全局模型实例（单例模式，双重检查锁保证线程安全）

_embedding_model: Optional[EmbeddingModel] = None
_embedding_init_lock = threading.Lock()


def get_embedding_model() -> EmbeddingModel:
    """获取全局向量化模型实例
    
    使用双重检查锁保证多线程环境下的安全单例初始化。
    
    Returns:
        EmbeddingModel: 向量化模型实例
    """
    global _embedding_model
    
    if _embedding_model is None:
        with _embedding_init_lock:
            if _embedding_model is None:
                _embedding_model = EmbeddingModel()
    
    return _embedding_model


def reset_embedding_model() -> None:
    """重置全局模型实例（用于测试）"""
    global _embedding_model
    with _embedding_init_lock:
        _embedding_model = None
