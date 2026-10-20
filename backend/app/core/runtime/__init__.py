"""系统运行时探测与资源预算模块。

为 CPU 密集型组件（向量化、解析等）提供:
- 物理/逻辑核数与亲和性探测 (cpu_runtime)
- 核数预算分配与线程上限应用 (cpu_budget)

设计目标:
- 启动时一次性探测，结果全进程共享
- 与 GPU 自适应（encode_gate 中的 EmbeddingBatchSizeController/
  GpuDeviceCooldownController）完全解耦
"""

from app.core.runtime.cpu_budget import (
    CpuBudget,
    apply_env_thread_limits,
    apply_torch_thread_limits,
    build_onnx_session_options,
    compute_cpu_budget,
    get_cpu_budget,
    reset_cpu_budget,
)
from app.core.runtime.cpu_runtime import (
    CpuTopology,
    detect_cpu_topology,
    get_cpu_topology,
    reset_cpu_topology,
)

__all__ = [
    "CpuTopology",
    "detect_cpu_topology",
    "get_cpu_topology",
    "reset_cpu_topology",
    "CpuBudget",
    "compute_cpu_budget",
    "get_cpu_budget",
    "reset_cpu_budget",
    "apply_env_thread_limits",
    "apply_torch_thread_limits",
    "build_onnx_session_options",
]
