"""CpuEncodeConcurrencyController 与 cpu_encode_semaphore 双缓冲替换单元测试。

覆盖:
- 控制器初始 / 升档 / 降档判定
- 滚动窗口未满时不调
- p95 latency / queue_wait / latency 翻倍 / load 越界各类降档触发
- success_streak + 冷却到期 + 信号良好的升档触发
- 窗口满前不锚定基线 / 锚定后再降档基于基线
- _resize_cpu_encode_semaphore 双缓冲替换 (旧 sem 仍可用)
- single ton 行为 + reset
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_controller(**overrides):
    """构造一个不依赖 settings 的纯净控制器, 默认 effective_cores=8。"""
    from app.core.vectorizer.cpu_encode_concurrency import (
        CpuEncodeConcurrencyController,
    )

    defaults = dict(
        initial=2,
        min_concurrency=1,
        max_concurrency=4,
        window_size=4,
        increase_cooldown_sec=0,
        latency_ceiling_ms=10000,
        queue_wait_ceiling_ms=2000,
        stable_samples=2,
        load_pressure_factor=10.0,  # 测试时把 load 阈值放高, 避免本机环境影响
        effective_cores=8,
    )
    defaults.update(overrides)
    return CpuEncodeConcurrencyController(**defaults)


@pytest.fixture(autouse=True)
def reset_singletons():
    from app.core.vectorizer import (
        cpu_encode_concurrency as concurrency_mod,
        encode_gate,
    )

    concurrency_mod.reset_cpu_encode_concurrency_controller()
    encode_gate._reset_cpu_encode_semaphore()
    yield
    concurrency_mod.reset_cpu_encode_concurrency_controller()
    encode_gate._reset_cpu_encode_semaphore()


def test_controller_initial_state():
    c = _make_controller()
    stats = c.get_stats()
    assert stats["current"] == 2
    assert stats["min"] == 1
    assert stats["max"] == 4
    assert stats["adjust_total"] == 0
    assert stats["baseline_latency_ms"] is None


def test_window_not_full_no_adjust():
    """窗口未满时即使全部成功也不调。"""
    c = _make_controller(window_size=4)
    c.record_metric(latency_ms=100, queue_wait_ms=10, success=True)
    c.record_metric(latency_ms=100, queue_wait_ms=10, success=True)
    c.record_metric(latency_ms=100, queue_wait_ms=10, success=True)
    # 只有 3 条, 窗口 4 → 不调
    assert c.get_stats()["current"] == 2
    assert c.get_stats()["adjust_total"] == 0


def test_increase_after_window_full_and_streak():
    """窗口满 + success_streak 达标 + 冷却已过 + 各信号 < 70% ceiling → 升档。"""
    c = _make_controller(
        window_size=4, stable_samples=2, increase_cooldown_sec=0,
        latency_ceiling_ms=10000, queue_wait_ceiling_ms=1000,
    )
    # 用极小的 latency 凑满窗口
    for _ in range(4):
        c.record_metric(latency_ms=100, queue_wait_ms=10, success=True)
    stats = c.get_stats()
    assert stats["current"] == 3
    assert stats["adjust_total"] == 1


def test_decrease_on_latency_ceiling():
    """p95 > latency_ceiling → 立即降档, 不需要等冷却。"""
    c = _make_controller(window_size=4, latency_ceiling_ms=200)
    for _ in range(4):
        c.record_metric(latency_ms=500, queue_wait_ms=10, success=True)
    assert c.get_stats()["current"] == 1
    assert c.get_stats()["adjust_total"] == 1


def test_decrease_on_queue_wait_ceiling():
    """queue_wait p95 > ceiling → 降档。"""
    c = _make_controller(window_size=4, queue_wait_ceiling_ms=100)
    for _ in range(4):
        c.record_metric(latency_ms=100, queue_wait_ms=500, success=True)
    assert c.get_stats()["current"] == 1


def test_decrease_on_latency_doubled_vs_baseline():
    """先用低延迟锚定基线, 再用高延迟触发"翻倍降档"。

    把 initial 设到 max, 让第一批无法触发升档, 干净地测降档判定。
    """
    c = _make_controller(
        initial=4,
        min_concurrency=1,
        max_concurrency=4,
        window_size=4,
        latency_ceiling_ms=100000,
        queue_wait_ceiling_ms=100000,
    )
    # 第一批稳定低延迟, 锚定基线 ~100ms; 因 current==max, 不会升档
    for _ in range(4):
        c.record_metric(latency_ms=100, queue_wait_ms=10, success=True)
    baseline = c.get_stats()["baseline_latency_ms"]
    assert baseline is not None
    assert baseline <= 100
    assert c.get_stats()["current"] == 4  # 未升档

    # 一次高延迟样本足以让窗口 p95 翻倍 (3x baseline) → 触发一次降档
    c.record_metric(latency_ms=baseline * 3, queue_wait_ms=10, success=True)
    assert c.get_stats()["current"] == 3
    assert c.get_stats()["adjust_total"] >= 1


def test_decrease_on_low_success_rate():
    """success_rate < 0.5 → 降档。"""
    c = _make_controller(window_size=4)
    c.record_metric(latency_ms=100, queue_wait_ms=10, success=False)
    c.record_metric(latency_ms=100, queue_wait_ms=10, success=False)
    c.record_metric(latency_ms=100, queue_wait_ms=10, success=False)
    c.record_metric(latency_ms=100, queue_wait_ms=10, success=True)
    # success_rate = 1/4 = 0.25 < 0.5
    assert c.get_stats()["current"] == 1


def test_decrease_on_load_pressure(monkeypatch):
    """归一化系统压力 > load_pressure_factor → 降档。"""
    c = _make_controller(
        window_size=4,
        load_pressure_factor=1.5,
        effective_cores=4,
        latency_ceiling_ms=99999,
        queue_wait_ceiling_ms=99999,
    )
    # 强制 _read_load_pressure 返回 2.0 (> 1.5)
    monkeypatch.setattr(c, "_read_load_pressure", lambda: 2.0)
    for _ in range(4):
        c.record_metric(latency_ms=100, queue_wait_ms=10, success=True)
    assert c.get_stats()["current"] == 1


def test_increase_clamped_to_max():
    """已经在 max 不再升档。"""
    c = _make_controller(initial=4, min_concurrency=1, max_concurrency=4)
    for _ in range(20):
        c.record_metric(latency_ms=50, queue_wait_ms=5, success=True)
    assert c.get_stats()["current"] == 4


def test_decrease_clamped_to_min():
    """已经在 min 不再降档。"""
    c = _make_controller(initial=1, min_concurrency=1, max_concurrency=4, latency_ceiling_ms=10)
    for _ in range(8):
        c.record_metric(latency_ms=10000, queue_wait_ms=10, success=True)
    assert c.get_stats()["current"] == 1


def test_resize_cpu_semaphore_double_buffer():
    """_resize_cpu_encode_semaphore 创建新 sem 并替换全局引用; 旧 sem 仍可用。"""
    from app.core.vectorizer.encode_gate import (
        _get_cpu_encode_semaphore,
        _resize_cpu_encode_semaphore,
        get_cpu_encode_capacity,
    )

    async def _run():
        old_sem = _get_cpu_encode_semaphore()
        old_cap = get_cpu_encode_capacity()
        # 给老 sem 占一个槽位
        await old_sem.acquire()
        try:
            # 触发 resize
            assert _resize_cpu_encode_semaphore(old_cap + 2) is True
            new_sem = _get_cpu_encode_semaphore()
            # 必须是新对象
            assert new_sem is not old_sem
            # 容量记账更新
            assert get_cpu_encode_capacity() == old_cap + 2
        finally:
            old_sem.release()

    asyncio.run(_run())


def test_resize_to_same_capacity_is_noop():
    """同容量 resize 返回 False, 不创建新 sem。"""
    from app.core.vectorizer.encode_gate import (
        _get_cpu_encode_semaphore,
        _resize_cpu_encode_semaphore,
        get_cpu_encode_capacity,
    )

    async def _run():
        sem1 = _get_cpu_encode_semaphore()
        cap = get_cpu_encode_capacity()
        assert _resize_cpu_encode_semaphore(cap) is False
        sem2 = _get_cpu_encode_semaphore()
        assert sem1 is sem2

    asyncio.run(_run())


def test_resize_via_apply_capacity():
    """控制器升档 / 降档时通过 _apply_capacity 改 semaphore 容量。"""
    from app.core.vectorizer.encode_gate import (
        _resize_cpu_encode_semaphore,
        get_cpu_encode_capacity,
    )

    async def _run():
        # 先建立 sem
        _resize_cpu_encode_semaphore(2)
        assert get_cpu_encode_capacity() == 2

        c = _make_controller(
            initial=2, min_concurrency=1, max_concurrency=4,
            window_size=4, stable_samples=2, increase_cooldown_sec=0,
        )
        # 触发升档 → 容量应同步成 3
        for _ in range(4):
            c.record_metric(latency_ms=50, queue_wait_ms=5, success=True)
        assert c.get_stats()["current"] == 3
        assert get_cpu_encode_capacity() == 3

    asyncio.run(_run())


def test_singleton_install_metric_handler():
    """get_cpu_encode_concurrency_controller 必须把 handler 注册到 encode_gate。"""
    from app.core.vectorizer import encode_gate
    from app.core.vectorizer.cpu_encode_concurrency import (
        get_cpu_encode_concurrency_controller,
        reset_cpu_encode_concurrency_controller,
    )

    reset_cpu_encode_concurrency_controller()
    assert encode_gate._cpu_metric_handler is None
    c = get_cpu_encode_concurrency_controller()
    assert encode_gate._cpu_metric_handler is not None

    # 通过 handler 投递 metric, 控制器内窗口应增长
    handler = encode_gate._cpu_metric_handler
    handler(encode_gate.CpuEncodeMetric(
        latency_ms=100,
        queue_wait_ms=10,
        batch_size=8,
        total_chars=400,
        success=True,
    ))
    assert c.get_stats()["samples_in_window"] == 1


def test_project_default_controller_uses_measured_sweet_spot():
    """项目默认 settings 应让 CPU 兜底从实测最优 concurrency=2 起步并封顶为 2。

    真模型基准显示 shared_session concurrency=2 优于 4，因此默认控制器不应再
    从 1 慢慢爬升，也不应升到 4。
    """
    from app.core.vectorizer.cpu_encode_concurrency import (
        get_cpu_encode_concurrency_controller,
    )

    c = get_cpu_encode_concurrency_controller()
    stats = c.get_stats()
    assert stats["current"] == 2
    assert stats["max"] == 2

    # 连续成功样本填满多个窗口，也不能超过 max=2
    for _ in range(12):
        c.record_metric(latency_ms=50, queue_wait_ms=1, success=True)
    stats = c.get_stats()
    assert stats["current"] == 2
    assert stats["max"] == 2


def test_metric_handler_exception_is_swallowed():
    """metric handler 内部抛异常不应影响 encode_gate 主流程。"""
    from app.core.vectorizer.encode_gate import (
        CpuEncodeMetric,
        _record_cpu_metric,
        set_cpu_metric_handler,
    )

    def _bad_handler(metric):
        raise RuntimeError("intentional")

    set_cpu_metric_handler(_bad_handler)
    try:
        _record_cpu_metric(CpuEncodeMetric(
            latency_ms=100, queue_wait_ms=10, batch_size=8,
            total_chars=400, success=True,
        ))  # 不抛
    finally:
        set_cpu_metric_handler(None)
