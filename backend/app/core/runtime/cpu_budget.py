"""CPU 核数预算分配与线程上限应用。

基于 ``cpu_runtime.CpuTopology`` 的 ``effective_cores``, 决定:

- ``intra_op_threads``: 单次 encode 内核计算线程数
                       (PyTorch torch.set_num_threads / ONNX SessionOptions.intra_op_num_threads)
- ``inter_op_threads``: 算子间并行线程, 对 bge-m3 来说 1 即可
- ``max_concurrency``:  CPU 路径同时允许多少路 encode 在飞,
                       由 CpuEncodeConcurrencyController + Semaphore 实施

设计要点:
- ``apply_thread_limits()`` 必须在第一次 import torch / 创建 tensor 之前调用,
  否则 PyTorch 已经把 OMP 线程池建好了, 后续设置不生效。本项目的合规调用点是
  ``backend/app/worker.py`` 顶层 (在业务 import 之前) 与 ``app.main`` 的 lifespan 早期。
- 预算计算只读 ``Settings`` + ``CpuTopology``, 无副作用; ``apply_*`` 系列才会修改环境/torch 状态。
- 单元测试通过显式传入 ``CpuTopology`` 与 mock ``settings`` 验证。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.core.runtime.cpu_runtime import CpuTopology, get_cpu_topology


@dataclass(frozen=True)
class CpuBudget:
    """CPU 路径资源预算。

    ``effective_cores`` 仅用于回显/日志, 真正的执行参数是后三项。
    """

    effective_cores: int
    intra_op_threads: int
    inter_op_threads: int
    max_concurrency: int

    def to_log_str(self) -> str:
        return (
            f"effective_cores={self.effective_cores}, "
            f"intra_op_threads={self.intra_op_threads}, "
            f"inter_op_threads={self.inter_op_threads}, "
            f"max_concurrency={self.max_concurrency}"
        )


_budget: Optional[CpuBudget] = None
_budget_lock = threading.Lock()
_thread_limits_applied = False
_thread_limits_lock = threading.Lock()


def _resolve_intra_op_threads(effective_cores: int, override: Optional[int]) -> int:
    """计算单路 encode 的内核线程数。

    默认策略: ``max(2, effective_cores // 4)``——给同时多路并发预留余量,
    例如 8 核 → 2 线程, 16 核 → 4 线程; 1-2 核机器仍保底 2 线程
    避免单路推理被压成串行。
    """

    if override is not None and override > 0:
        return int(override)
    return max(2, effective_cores // 4)


def _resolve_max_concurrency(
    effective_cores: int,
    intra_op_threads: int,
    override: Optional[int],
) -> int:
    """计算 CPU 路径同时并发路数上限。

    默认策略: ``effective_cores // intra_op_threads`` 并 clamp >=1。
    例如 8 核 / 2 线程 = 4 路并发; 16 核 / 4 线程 = 4 路并发。
    实际 AIMD 会从 ``VECTOR_CPU_CONCURRENCY_INITIAL`` 起步爬升, 永不超过该上限。
    """

    if override is not None and override > 0:
        return int(override)
    return max(1, effective_cores // max(1, intra_op_threads))


def compute_cpu_budget(
    topology: Optional[CpuTopology] = None,
    *,
    intra_op_override: Optional[int] = None,
    inter_op_override: Optional[int] = None,
    max_concurrency_override: Optional[int] = None,
    inter_op_default: int = 1,
) -> CpuBudget:
    """根据拓扑与显式覆盖值计算预算; 不带任何副作用。

    Args:
        topology: CPU 拓扑; 不传则懒探测。
        intra_op_override: 显式 intra_op_threads, None=自动。
        inter_op_override: 显式 inter_op_threads, None=使用 inter_op_default。
        max_concurrency_override: 显式 max_concurrency, None=自动。
        inter_op_default: 默认的 inter_op_threads 值; 通过 settings 注入。
    """

    if topology is None:
        topology = get_cpu_topology()

    intra_op = _resolve_intra_op_threads(topology.effective_cores, intra_op_override)
    inter_op = (
        int(inter_op_override)
        if inter_op_override is not None and inter_op_override > 0
        else int(inter_op_default)
    )
    max_concurrency = _resolve_max_concurrency(
        topology.effective_cores, intra_op, max_concurrency_override
    )

    return CpuBudget(
        effective_cores=topology.effective_cores,
        intra_op_threads=intra_op,
        inter_op_threads=inter_op,
        max_concurrency=max_concurrency,
    )


def get_cpu_budget() -> CpuBudget:
    """从 ``settings`` 与缓存拓扑解析预算; 全进程单例。

    首次解析后缓存; 设置项变化需要重启 worker 才能生效——这与
    OMP_NUM_THREADS 必须在 import torch 前生效的约束一致。
    """

    global _budget
    if _budget is not None:
        return _budget
    with _budget_lock:
        if _budget is not None:
            return _budget
        from app.core.config import settings

        topology = get_cpu_topology()
        budget = compute_cpu_budget(
            topology,
            intra_op_override=getattr(settings, "VECTOR_CPU_INTRA_OP_THREADS", None),
            inter_op_override=getattr(settings, "VECTOR_CPU_INTER_OP_THREADS", 1),
            max_concurrency_override=getattr(
                settings, "VECTOR_CPU_MAX_CONCURRENCY", None
            ),
            inter_op_default=int(getattr(settings, "VECTOR_CPU_INTER_OP_THREADS", 1)),
        )
        _budget = budget
        logger.info(f"CpuBudget 计算: {budget.to_log_str()}")
        return budget


def reset_cpu_budget() -> None:
    """清除单例缓存, 仅供单元测试使用。"""

    global _budget, _thread_limits_applied
    with _budget_lock:
        _budget = None
    with _thread_limits_lock:
        _thread_limits_applied = False


def apply_env_thread_limits(budget: Optional[CpuBudget] = None) -> CpuBudget:
    """设置 OMP/MKL/OpenBLAS 环境变量, 必须在 import torch 之前调用。

    本函数幂等; 重复调用只有第一次会生效, 之后只读 budget。
    """

    global _thread_limits_applied
    if budget is None:
        budget = get_cpu_budget()

    with _thread_limits_lock:
        if _thread_limits_applied:
            return budget

        intra = str(budget.intra_op_threads)
        # 仅当用户/容器没有显式设过时才覆盖, 避免破坏外部编排
        for env_name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            if not os.environ.get(env_name):
                os.environ[env_name] = intra
        # ONNX Runtime 在 InferenceSession 创建前读取这两个环境变量
        if not os.environ.get("ORT_INTRA_OP_NUM_THREADS"):
            os.environ["ORT_INTRA_OP_NUM_THREADS"] = intra
        if not os.environ.get("ORT_INTER_OP_NUM_THREADS"):
            os.environ["ORT_INTER_OP_NUM_THREADS"] = str(budget.inter_op_threads)

        _thread_limits_applied = True
        logger.info(
            f"CpuBudget 已应用环境变量: OMP_NUM_THREADS={intra}, "
            f"MKL_NUM_THREADS={intra}, OPENBLAS_NUM_THREADS={intra}, "
            f"ORT_INTRA_OP_NUM_THREADS={intra}, "
            f"ORT_INTER_OP_NUM_THREADS={budget.inter_op_threads}"
        )
    return budget


def apply_torch_thread_limits(budget: Optional[CpuBudget] = None) -> CpuBudget:
    """在 ``torch`` 已 import 的情况下显式设置线程数。

    与 ``apply_env_thread_limits`` 配合使用: 后者负责进程启动早期,
    前者负责"已经 import torch 但还没创建 tensor"的二次保险。
    """

    if budget is None:
        budget = get_cpu_budget()

    try:
        import torch  # noqa: WPS433

        torch.set_num_threads(budget.intra_op_threads)
        try:
            torch.set_num_interop_threads(budget.inter_op_threads)
        except RuntimeError:
            # set_num_interop_threads 必须在并行工作开始前调用; 已被使用过则会抛 RuntimeError, 安全忽略
            pass
        logger.debug(
            f"CpuBudget 已应用 torch 线程上限: intra={budget.intra_op_threads}, "
            f"inter={budget.inter_op_threads}"
        )
    except ImportError:
        # torch 未安装的环境(纯 ONNX backend)安全跳过
        pass
    return budget


def build_onnx_session_options(budget: Optional[CpuBudget] = None):
    """构造一份 ``onnxruntime.SessionOptions`` 并应用线程上限。

    返回 ``None`` 表示 onnxruntime 未安装, 调用方应回退默认 SessionOptions。
    """

    if budget is None:
        budget = get_cpu_budget()

    try:
        import onnxruntime as ort  # noqa: WPS433

        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = budget.intra_op_threads
        sess_options.inter_op_num_threads = budget.inter_op_threads
        sess_options.enable_mem_pattern = True
        sess_options.enable_cpu_mem_arena = True
        return sess_options
    except ImportError:
        return None
