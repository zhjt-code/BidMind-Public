""" 防御：OCR 拥堵判定自死锁修复

历史背景:
 `OcrAdaptiveConcurrency.is_congested` 仅依赖 `_prev_queue_p95`(历史排队 p95)，
 一旦某次任务把 p95 冲过阈值，后续 `_try_ocr_full_page` 会全数命中拥堵分支
 → 直接 return 不发请求 → record 不被调用 → _prev_queue_p95 永不更新
 → is_congested 永远 True → 整文档全程降级 VL 自死锁。

修复:
 在 `is_congested` 增加两道自愈短路:
 - 空窗口短路: 信号量空闲 + 度量窗口已空 → 视为非拥堵
 - 陈旧度护栏: _prev_queue_p95_updated_at 超过 OCR_CONGESTION_STALE_SEC → 视为非拥堵
 并在 `_maybe_adjust` 内每次写入 _prev_queue_p95 时同步更新 _prev_queue_p95_updated_at

防御目标:
 1. 陈旧度自愈正确触发
 2. 空窗口短路正确触发
 3. 真实拥堵期间不会被误判为非拥堵
 4. _maybe_adjust 必须更新时间戳（防回归 → 否则陈旧度护栏永远不会被解除）
"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def controller():
    """构造一个独立的 OcrAdaptiveConcurrency 实例。

    Python 3.10+ asyncio.Lock/Event 不强绑定 loop，可在同步上下文构造。
    """
    from app.core.parser.adaptive_concurrency import OcrAdaptiveConcurrency

    return OcrAdaptiveConcurrency(instance_count=1)


@pytest.fixture
def make_metric():
    """构造 OcrRequestMetric 的便捷工厂。"""
    from app.core.parser.adaptive_concurrency import OcrRequestMetric

    def _make(queue_wait_ms: float = 0.0, latency_ms: float = 100.0) -> OcrRequestMetric:
        return OcrRequestMetric(
            latency_ms=latency_ms,
            queue_wait_ms=queue_wait_ms,
            success=True,
            is_timeout=False,
            is_transport_error=False,
            is_low_confidence=False,
            weight=1,
        )

    return _make


# ---------------------------------------------------------------------------
# 场景 1: 基础短路（_prev_queue_p95 从未超阈值）
# ---------------------------------------------------------------------------


def test_basic_short_circuit_when_no_history(controller):
    """初始状态 _prev_queue_p95 = None → 永远非拥堵。"""
    assert controller._prev_queue_p95 is None
    assert controller.is_congested is False


def test_basic_short_circuit_when_below_threshold(controller):
    """_prev_queue_p95 低于阈值 → 非拥堵。"""
    from app.core.config import settings

    controller._prev_queue_p95 = settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS - 1
    controller._prev_queue_p95_updated_at = time.monotonic()
    assert controller.is_congested is False


# ---------------------------------------------------------------------------
# 场景 2: 空窗口短路
# ---------------------------------------------------------------------------


def test_empty_window_short_circuits_when_idle(controller):
    """信号量空闲 + 度量窗口为空 → 视为非拥堵（即使 _prev_queue_p95 > 阈值）。

 这是 死锁的关键解锁条件: 没有在途请求 + 没有最近样本 →
 历史拥堵信号已无现实依据，必须放行下一次 OCR 请求来获取新样本。
 """
    from app.core.config import settings

    controller._prev_queue_p95 = settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS * 2
    controller._prev_queue_p95_updated_at = time.monotonic()
    controller._metrics.clear()
    assert controller._semaphore.used == 0
    assert len(controller._metrics) == 0

    assert controller.is_congested is False, (
        ": 信号量空闲 + 度量窗口已空时仍判定拥堵会导致 OCR 永久降级 VL 自死锁"
    )


def test_empty_window_does_not_short_circuit_when_in_use(controller, make_metric):
    """信号量被占用时不能因窗口空就放行（仍可能真的拥堵）。"""
    from app.core.config import settings

    controller._prev_queue_p95 = settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS * 2
    controller._prev_queue_p95_updated_at = time.monotonic()
    controller._metrics.clear()
    controller._semaphore._used = 1

    assert controller.is_congested is True


# ---------------------------------------------------------------------------
# 场景 3: 陈旧度护栏
# ---------------------------------------------------------------------------


def test_stale_p95_self_heals(controller, make_metric):
    """_prev_queue_p95_updated_at 超过 OCR_CONGESTION_STALE_SEC → 视为非拥堵。

    防御场景: 单次历史尖峰把 p95 冲高，后续无人请求 OCR，时间一过自动愈合。
    """
    from app.core.config import settings

    controller._prev_queue_p95 = settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS * 2
    stale_age = settings.OCR_CONGESTION_STALE_SEC + 5
    controller._prev_queue_p95_updated_at = time.monotonic() - stale_age
    # 故意保留一些"陈旧的高样本"在窗口里，验证陈旧度护栏不会被旧 metrics 干扰
    controller._metrics.append(make_metric(queue_wait_ms=20000.0))
    controller._metrics.append(make_metric(queue_wait_ms=20000.0))
    controller._semaphore._used = 0

    assert controller.is_congested is False, (
        ": _prev_queue_p95 长时间未更新仍判定拥堵会导致 worker 重启前永远无法恢复 OCR"
    )


def test_fresh_p95_keeps_congested(controller, make_metric):
    """时间戳新鲜 + 真实繁忙 → 保持拥堵判定（防止护栏过度放水）。"""
    from app.core.config import settings

    controller._prev_queue_p95 = settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS * 2
    controller._prev_queue_p95_updated_at = time.monotonic()
    controller._semaphore._used = 1
    for _ in range(controller._FAST_RECOVERY_SAMPLE):
        controller._metrics.append(
            make_metric(queue_wait_ms=settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS * 2)
        )

    assert controller.is_congested is True


def test_stale_threshold_disabled_when_zero(controller, make_metric):
    """OCR_CONGESTION_STALE_SEC <= 0 时陈旧度护栏关闭（运维兜底开关）。"""
    from app.core.config import settings

    original = settings.OCR_CONGESTION_STALE_SEC
    settings.OCR_CONGESTION_STALE_SEC = 0
    try:
        controller._prev_queue_p95 = settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS * 2
        # 时间戳很旧
        controller._prev_queue_p95_updated_at = time.monotonic() - 9999
        controller._semaphore._used = 1
        for _ in range(controller._FAST_RECOVERY_SAMPLE):
            controller._metrics.append(
                make_metric(queue_wait_ms=settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS * 2)
            )
        assert controller.is_congested is True
    finally:
        settings.OCR_CONGESTION_STALE_SEC = original


# ---------------------------------------------------------------------------
# 场景 4: 既有快速恢复分支不应被覆盖
# ---------------------------------------------------------------------------


def test_fast_recovery_branch_still_works(controller, make_metric):
    """既有"信号量空闲 + 最近 N 条样本远低于阈值"快速恢复路径必须保留。"""
    from app.core.config import settings

    threshold = settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS
    controller._prev_queue_p95 = threshold * 2
    controller._prev_queue_p95_updated_at = time.monotonic()
    controller._semaphore._used = 0
    controller._metrics.clear()
    for _ in range(controller._FAST_RECOVERY_SAMPLE):
        controller._metrics.append(make_metric(queue_wait_ms=threshold * 0.1))

    assert controller.is_congested is False


# ---------------------------------------------------------------------------
# 场景 5: 时间戳更新路径（防回归）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_adjust_updates_timestamp(make_metric):
    """_maybe_adjust 执行后必须写入 _prev_queue_p95_updated_at。

    防回归: 否则陈旧度护栏永远不会"刷新"，反而把恢复路径堵死。
    """
    from app.core.parser.adaptive_concurrency import OcrAdaptiveConcurrency

    controller = OcrAdaptiveConcurrency(instance_count=1)
    assert controller._prev_queue_p95_updated_at is None

    # 灌满窗口触发 _maybe_adjust
    for _ in range(controller._window_size):
        await controller.record(make_metric(queue_wait_ms=100.0))

    assert controller._prev_queue_p95 is not None, "_maybe_adjust 应已写入 _prev_queue_p95"
    assert controller._prev_queue_p95_updated_at is not None, (
        " 防回归: _maybe_adjust 必须同步更新 _prev_queue_p95_updated_at"
    )
    # 时间戳应在最近几秒内
    age = time.monotonic() - controller._prev_queue_p95_updated_at
    assert 0 <= age < 5


# ---------------------------------------------------------------------------
# 场景 6: 端到端死锁解锁仿真
# ---------------------------------------------------------------------------


def test_deadlock_scenario_recovers_after_idle_window(controller):
    """端到端: 模拟原 BUG 场景，新逻辑应能解锁。

    步骤:
    1. 注入历史拥堵态: _prev_queue_p95 高 + 时间戳新鲜 + metrics 不空 → 拥堵
    2. 模拟"无新请求 + 时间流逝": 清空窗口 + 时间戳变陈旧 → 拥堵自愈
    """
    from app.core.config import settings

    threshold = settings.OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS

    controller._prev_queue_p95 = threshold * 3
    controller._prev_queue_p95_updated_at = time.monotonic()
    controller._semaphore._used = 1  # 模拟有 1 个请求在跑
    controller._metrics.clear()
    from app.core.parser.adaptive_concurrency import OcrRequestMetric
    for _ in range(controller._FAST_RECOVERY_SAMPLE):
        controller._metrics.append(OcrRequestMetric(
            latency_ms=200.0,
            queue_wait_ms=threshold * 3,
            success=True,
            is_timeout=False,
            is_transport_error=False,
            is_low_confidence=False,
            weight=1,
        ))
    assert controller.is_congested is True, "初始应判定拥堵"

    # 模拟 OCR 全部完成 + 没有新请求 + 时间流逝
    controller._semaphore._used = 0
    controller._metrics.clear()
    controller._prev_queue_p95_updated_at = time.monotonic() - (
        settings.OCR_CONGESTION_STALE_SEC + 1
    )
    assert controller.is_congested is False, (
        ": 死锁场景必须能在 OCR_CONGESTION_STALE_SEC 后自动解锁"
    )
