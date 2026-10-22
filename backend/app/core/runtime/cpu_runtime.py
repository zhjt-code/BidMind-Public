"""CPU 拓扑探测。

针对 CPU 路径自适应改造的"前置层"——启动时一次性把当前进程
真正可以使用的核数算清楚，作为后续预算分配 (cpu_budget) 与
并发控制 (CpuEncodeConcurrencyController) 的基准。

探测维度:
- 物理核 (physical): psutil.cpu_count(logical=False)
- 逻辑核 (logical):  psutil.cpu_count(logical=True)
- 亲和性 (affinity): psutil.Process().cpu_affinity() 长度
                     反映容器/任务管理器对当前进程的核绑定
- cgroup quota:     Linux 容器场景 cgroup v2 (cpu.max) 或
                     v1 (cpu.cfs_quota_us / cpu.cfs_period_us)
                     给出"等效核数 = quota / period"

最终输出 effective_cores = min(物理, 亲和性, cgroup 等效)，并 clamp >=1。

注意:
- Windows 没有 cgroup 文件，cgroup 维度返回 None 跳过
- psutil 在 Windows 跨 ProcessGroup 已自动汇总，不需特殊处理
- 探测一次后缓存为模块级单例；reset_cpu_topology() 仅供测试使用
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass(frozen=True)
class CpuTopology:
    """系统 CPU 拓扑快照。

    所有字段都为正整数；当前 effective_cores 是后续预算分配的唯一锚点。
    cgroup_cores 为 None 表示"不在 cgroup 限额下"或"无法读取"，与"等效核数 0"
    严格区分。
    """

    physical: int
    logical: int
    affinity: int
    cgroup_cores: Optional[float]
    effective_cores: int
    platform: str
    source_notes: str

    def to_log_str(self) -> str:
        """单行日志风格，与 OcrAdaptiveConcurrency 初始化日志保持一致格式。"""
        cgroup_str = (
            f"{self.cgroup_cores:.2f}"
            if self.cgroup_cores is not None
            else "unlimited"
        )
        return (
            f"physical={self.physical}, logical={self.logical}, "
            f"affinity={self.affinity}, cgroup={cgroup_str}, "
            f"effective={self.effective_cores}, platform={self.platform}"
        )


_topology: Optional[CpuTopology] = None
_topology_lock = threading.Lock()


def _read_cgroup_v2_cores() -> Optional[float]:
    """读取 cgroup v2 的 cpu.max 文件，解析 quota period 换算等效核数。"""
    candidates = [
        Path("/sys/fs/cgroup/cpu.max"),
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8").strip()
            parts = raw.split()
            if len(parts) != 2:
                continue
            quota_str, period_str = parts
            if quota_str == "max":
                return None
            quota = float(quota_str)
            period = float(period_str)
            if quota <= 0 or period <= 0:
                return None
            return quota / period
        except Exception:
            continue
    return None


def _read_cgroup_v1_cores() -> Optional[float]:
    """读取 cgroup v1 的 cpu.cfs_quota_us / cpu.cfs_period_us。"""
    quota_paths = [
        Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
        Path("/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us"),
    ]
    period_paths = [
        Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us"),
        Path("/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us"),
    ]

    quota_value: Optional[float] = None
    for path in quota_paths:
        try:
            if path.is_file():
                quota_value = float(path.read_text(encoding="utf-8").strip())
                break
        except Exception:
            continue
    if quota_value is None or quota_value <= 0:
        return None

    period_value: Optional[float] = None
    for path in period_paths:
        try:
            if path.is_file():
                period_value = float(path.read_text(encoding="utf-8").strip())
                break
        except Exception:
            continue
    if period_value is None or period_value <= 0:
        return None
    return quota_value / period_value


def _read_cgroup_cores() -> Optional[float]:
    """Linux 下尝试 cgroup v2、v1，二者均失败时返回 None。"""
    if sys.platform.startswith("linux"):
        v2 = _read_cgroup_v2_cores()
        if v2 is not None:
            return v2
        return _read_cgroup_v1_cores()
    return None


def _detect_affinity_count(logical_fallback: int) -> int:
    """优先 psutil.cpu_affinity，缺失则尝试 os.sched_getaffinity，最终 fallback 逻辑核。"""
    try:
        import psutil

        affinity = psutil.Process().cpu_affinity()
        if affinity:
            return len(affinity)
    except Exception:
        pass

    if hasattr(os, "sched_getaffinity"):
        try:
            return len(os.sched_getaffinity(0))
        except Exception:
            pass

    return max(1, logical_fallback)


def detect_cpu_topology() -> CpuTopology:
    """执行一次完整 CPU 拓扑探测。

    本函数无副作用；调用方通常应使用 ``get_cpu_topology()`` 走单例缓存。
    """

    notes: list[str] = []

    try:
        import psutil

        physical = int(psutil.cpu_count(logical=False) or 0)
        logical = int(psutil.cpu_count(logical=True) or 0)
    except Exception as exc:
        notes.append(f"psutil_unavailable:{exc}")
        physical = 0
        logical = 0

    if logical <= 0:
        # psutil 缺失时退化到 os.cpu_count
        logical = int(os.cpu_count() or 1)
        notes.append("logical_fallback_os_cpu_count")
    if physical <= 0:
        physical = max(1, logical // 2)
        notes.append("physical_fallback_logical_half")

    affinity = _detect_affinity_count(logical)

    cgroup_cores = _read_cgroup_cores()

    candidates = [physical, affinity]
    if cgroup_cores is not None and cgroup_cores > 0:
        candidates.append(int(max(1, round(cgroup_cores))))
    effective = max(1, min(candidates))

    topology = CpuTopology(
        physical=physical,
        logical=logical,
        affinity=affinity,
        cgroup_cores=cgroup_cores,
        effective_cores=effective,
        platform=sys.platform,
        source_notes=";".join(notes) if notes else "ok",
    )
    return topology


def get_cpu_topology() -> CpuTopology:
    """全进程缓存的 CPU 拓扑；首次调用会触发探测并打印一行日志。

    日志格式参考 ``OcrAdaptiveConcurrency`` 初始化日志，便于运维一眼对照。
    """

    global _topology
    if _topology is not None:
        return _topology
    with _topology_lock:
        if _topology is not None:
            return _topology
        topology = detect_cpu_topology()
        _topology = topology
        logger.info(f"CpuTopology 探测: {topology.to_log_str()}")
        return topology


def reset_cpu_topology() -> None:
    """清除单例缓存，仅供单元测试使用。"""

    global _topology
    with _topology_lock:
        _topology = None
