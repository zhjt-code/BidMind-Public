"""按 cgroup 配额设置 CPU 线程数。

职责: 让 Paddle 线程数等于容器 CPU 配额
不做: 不改业务逻辑、不探测 GPU
"""

from __future__ import annotations

import os
from pathlib import Path


def cpu_thread_quota() -> int:
    """cgroup 配额优先; 无配额时用逻辑核数的 80%。"""
    quota = _cgroup_cpus()
    if quota is not None:
        return max(1, quota)
    logical = os.cpu_count() or 1
    return max(1, int(logical * 0.8))


def apply_cpu_thread_quota() -> int:
    """在导入 Paddle 之前调用。"""
    threads = str(cpu_thread_quota())
    os.environ["OMP_NUM_THREADS"] = threads
    os.environ["MKL_NUM_THREADS"] = threads
    return int(threads)


def _cgroup_cpus() -> int | None:
    path = Path("/sys/fs/cgroup/cpu.max")
    try:
        quota_text, period_text = path.read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return None
    if quota_text == "max":
        return None
    period = int(period_text)
    if period <= 0:
        return None
    return max(1, int(int(quota_text) / period))
