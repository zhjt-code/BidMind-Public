"""cpu_budget 预算计算与线程上限应用单元测试。

覆盖:
- intra_op_threads 的默认公式 max(2, effective_cores // 4)
- max_concurrency 的默认公式 effective_cores // intra_op_threads
- override 优先级
- apply_env_thread_limits 写入环境变量且幂等
- build_onnx_session_options 构造 SessionOptions 含正确的线程数
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def reset_state():
    from app.core.runtime import cpu_budget, cpu_runtime

    cpu_budget.reset_cpu_budget()
    cpu_runtime.reset_cpu_topology()
    yield
    cpu_budget.reset_cpu_budget()
    cpu_runtime.reset_cpu_topology()


def _make_topo(cores: int):
    from app.core.runtime.cpu_runtime import CpuTopology

    return CpuTopology(
        physical=cores,
        logical=cores * 2,
        affinity=cores,
        cgroup_cores=None,
        effective_cores=cores,
        platform="test",
        source_notes="ok",
    )


@pytest.mark.parametrize(
    "effective,expected_intra,expected_concurrency",
    [
        (1, 2, 1),    # max(2, 0) = 2; 1//2 = 0 → clamp 1
        (2, 2, 1),    # max(2, 0) = 2; 2//2 = 1
        (4, 2, 2),    # max(2, 1) = 2; 4//2 = 2
        (8, 2, 4),    # max(2, 2) = 2; 8//2 = 4
        (16, 4, 4),   # max(2, 4) = 4; 16//4 = 4
        (32, 8, 4),   # max(2, 8) = 8; 32//8 = 4
    ],
)
def test_default_budget_formula(effective, expected_intra, expected_concurrency):
    """默认预算公式: intra=max(2, cores//4), concurrency=cores//intra。"""
    from app.core.runtime.cpu_budget import compute_cpu_budget

    budget = compute_cpu_budget(_make_topo(effective))
    assert budget.intra_op_threads == expected_intra
    assert budget.max_concurrency == expected_concurrency
    assert budget.effective_cores == effective


def test_intra_op_override_takes_precedence():
    """显式传入 intra_op_override 时, 公式被覆盖。"""
    from app.core.runtime.cpu_budget import compute_cpu_budget

    budget = compute_cpu_budget(_make_topo(8), intra_op_override=6)
    assert budget.intra_op_threads == 6
    # max_concurrency = 8 // 6 = 1
    assert budget.max_concurrency == 1


def test_max_concurrency_override_takes_precedence():
    """显式传入 max_concurrency_override 时, 公式被覆盖。"""
    from app.core.runtime.cpu_budget import compute_cpu_budget

    budget = compute_cpu_budget(_make_topo(8), max_concurrency_override=10)
    assert budget.max_concurrency == 10


def test_inter_op_default_is_one():
    """inter_op_threads 默认值 1, 适合 bge-m3 这类对算子并行不敏感的模型。"""
    from app.core.runtime.cpu_budget import compute_cpu_budget

    budget = compute_cpu_budget(_make_topo(8))
    assert budget.inter_op_threads == 1


def test_apply_env_thread_limits_writes_env(monkeypatch):
    """首次 apply 后 OMP/MKL/ORT 环境变量应被设置成 intra_op_threads。"""
    from app.core.runtime.cpu_budget import (
        CpuBudget,
        apply_env_thread_limits,
        reset_cpu_budget,
    )

    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
    monkeypatch.delenv("ORT_INTRA_OP_NUM_THREADS", raising=False)
    monkeypatch.delenv("ORT_INTER_OP_NUM_THREADS", raising=False)

    reset_cpu_budget()
    fake = CpuBudget(
        effective_cores=8,
        intra_op_threads=4,
        inter_op_threads=1,
        max_concurrency=2,
    )
    apply_env_thread_limits(fake)

    assert os.environ["OMP_NUM_THREADS"] == "4"
    assert os.environ["MKL_NUM_THREADS"] == "4"
    assert os.environ["OPENBLAS_NUM_THREADS"] == "4"
    assert os.environ["ORT_INTRA_OP_NUM_THREADS"] == "4"
    assert os.environ["ORT_INTER_OP_NUM_THREADS"] == "1"


def test_apply_env_thread_limits_is_idempotent(monkeypatch):
    """重复 apply 不会改写已被外部设置过的值。"""
    from app.core.runtime.cpu_budget import (
        CpuBudget,
        apply_env_thread_limits,
        reset_cpu_budget,
    )

    reset_cpu_budget()
    monkeypatch.setenv("OMP_NUM_THREADS", "100")  # 模拟外部已设
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)

    fake = CpuBudget(
        effective_cores=8, intra_op_threads=4, inter_op_threads=1, max_concurrency=2
    )
    apply_env_thread_limits(fake)
    # 已存在的不覆盖
    assert os.environ["OMP_NUM_THREADS"] == "100"
    # 缺失的填充
    assert os.environ["MKL_NUM_THREADS"] == "4"


def test_apply_env_thread_limits_only_runs_once(monkeypatch):
    """模块级幂等: 第二次调用是 no-op。"""
    from app.core.runtime.cpu_budget import (
        CpuBudget,
        apply_env_thread_limits,
        reset_cpu_budget,
    )

    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    reset_cpu_budget()
    fake1 = CpuBudget(
        effective_cores=8, intra_op_threads=4, inter_op_threads=1, max_concurrency=2
    )
    apply_env_thread_limits(fake1)
    assert os.environ["OMP_NUM_THREADS"] == "4"

    # 第二次传不同 budget 应被忽略 (幂等)
    fake2 = CpuBudget(
        effective_cores=4, intra_op_threads=2, inter_op_threads=1, max_concurrency=2
    )
    apply_env_thread_limits(fake2)
    assert os.environ["OMP_NUM_THREADS"] == "4"  # 没变


def test_build_onnx_session_options_includes_threads():
    """build_onnx_session_options 必须把 intra_op/inter_op_num_threads 写到 SessionOptions。"""
    pytest.importorskip("onnxruntime")
    from app.core.runtime.cpu_budget import CpuBudget, build_onnx_session_options

    fake = CpuBudget(
        effective_cores=8, intra_op_threads=3, inter_op_threads=2, max_concurrency=2
    )
    sess = build_onnx_session_options(fake)
    assert sess is not None
    assert sess.intra_op_num_threads == 3
    assert sess.inter_op_num_threads == 2
    # 内存模式应启用
    assert sess.enable_mem_pattern is True
    assert sess.enable_cpu_mem_arena is True


def test_singleton_cache():
    """get_cpu_budget 是单例; reset 后重新计算。"""
    from app.core.runtime.cpu_budget import get_cpu_budget, reset_cpu_budget

    a = get_cpu_budget()
    b = get_cpu_budget()
    assert a is b
    reset_cpu_budget()
    c = get_cpu_budget()
    assert c is not a


def test_project_default_budget_uses_measured_cpu_sweet_spot():
    """项目默认配置应固定到真模型基准测出的 CPU 最优兜底点。

    当前机器实测 shared_session concurrency=2 最优, concurrency=4 反向降速。
    因此 settings 默认值不再走纯公式的 max_concurrency=4, 而是显式收敛到 2。
    """
    from app.core.runtime.cpu_budget import get_cpu_budget

    budget = get_cpu_budget()
    assert budget.intra_op_threads == 4
    assert budget.inter_op_threads == 1
    assert budget.max_concurrency == 2
