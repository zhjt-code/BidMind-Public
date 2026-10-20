"""Dispatcher AIMD 自适应并发控制器单元测试

验证 DispatcherConcurrencyController 在不同信号序列下的收敛行为：
- 全部健康 -> 逐步增加并发
- 出现 429 -> 乘法减少
- 连续故障 -> 重度减少
- 尾延迟抖动 -> 默认只观测不降容，开关打开后才加法减少
- 自然重尾全成功 -> 容量不得棘轮式压到 min，且能回升
- 下限保护 -> 不低于 min
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.dispatcher import AIDispatcher
from app.core.ai.dispatcher_concurrency import (
    DispatcherConcurrencyController,
    DispatcherRequestMetric,
)
from app.core.config import settings


def _make_metric(
    *,
    latency_ms: float = 100.0,
    success: bool = True,
    is_rate_limited: bool = False,
    is_timeout: bool = False,
) -> DispatcherRequestMetric:
    return DispatcherRequestMetric(
        latency_ms=latency_ms,
        success=success,
        is_rate_limited=is_rate_limited,
        is_timeout=is_timeout,
    )


# ==================== 合成重尾形状夹具====================
# 重要说明: 以下数值是按"LLM 输出长度差异导致的自然重尾"形状人工构造的合成序列，
# 不是任何真实项目的脱敏采样数据，绝对值不具备生产代表性，仅形状特征有意义:
#   全部 success、无 429、无 timeout；主体 3.2-7.4s；约每 14 条出现一条 41-52s 的长输出。
# 尖峰间距 14 < 窗口 15，保证每个满窗都至少含一条尖峰，即 p95(=窗口成功样本最大值)
# 恒远大于 p50*3 —— 这是真实故障"全部成功却持续触发尾延迟降档"的最坏形状。
_TAIL_SHAPE_BODY_MS = (3200.0, 5100.0, 4300.0, 6800.0, 3900.0, 7400.0, 5600.0)
_TAIL_SHAPE_SPIKE_MS = (41000.0, 52000.0, 47000.0)
_TAIL_SHAPE_SPIKE_EVERY = 14


def _heavy_tail_latencies(count: int) -> list[float]:
    """生成合成重尾延迟序列（非真实采样，见上方说明）"""
    out: list[float] = []
    for i in range(count):
        if i > 0 and i % _TAIL_SHAPE_SPIKE_EVERY == 0:
            spike_idx = (i // _TAIL_SHAPE_SPIKE_EVERY - 1) % len(_TAIL_SHAPE_SPIKE_MS)
            out.append(_TAIL_SHAPE_SPIKE_MS[spike_idx])
        else:
            out.append(_TAIL_SHAPE_BODY_MS[i % len(_TAIL_SHAPE_BODY_MS)])
    return out


def _make_controller(
    channel_count: int = 4,
    initial_per_channel: int = 15,
    min_per_channel: int = 3,
    max_per_channel: int = 25,
    window_size: int = 5,
    increase_cooldown: int = 0,
    latency_ceiling: int = 120000,
) -> DispatcherConcurrencyController:
    return DispatcherConcurrencyController(
        channel_count=channel_count,
        initial_per_channel=initial_per_channel,
        min_per_channel=min_per_channel,
        max_per_channel=max_per_channel,
        window_size=window_size,
        increase_cooldown=increase_cooldown,
        latency_ceiling=latency_ceiling,
        purpose="test",
    )


class TestInitialization:
    def test_initial_capacity(self):
        ctrl = _make_controller(channel_count=4, initial_per_channel=15)
        assert ctrl.current == 60

    def test_min_max_bounds(self):
        ctrl = _make_controller(
            channel_count=2, min_per_channel=3, max_per_channel=25,
        )
        assert ctrl._min_c == 6
        assert ctrl._max_c == 50

    def test_single_channel(self):
        ctrl = _make_controller(channel_count=1, initial_per_channel=15)
        assert ctrl.current == 15
        assert ctrl._min_c == 3

    def test_vision_dispatcher_uses_vision_specific_settings(self, monkeypatch):
        class _FakeClient:
            base_url = "https://example.com/v1"
            model_name = "vision-model"

        monkeypatch.setattr(settings, "VISION_CONCURRENCY_CLOUD_INITIAL", 2)
        monkeypatch.setattr(settings, "VISION_CONCURRENCY_CLOUD_MIN", 1)
        monkeypatch.setattr(settings, "VISION_CONCURRENCY_CLOUD_MAX", 5)
        monkeypatch.setattr(settings, "VISION_CONCURRENCY_WINDOW_SIZE", 7)
        monkeypatch.setattr(settings, "VISION_CONCURRENCY_INCREASE_COOLDOWN", 3)
        monkeypatch.setattr(settings, "VISION_CONCURRENCY_LATENCY_CEILING", 9000)

        dispatcher = AIDispatcher(purpose="vision")
        dispatcher.register_channel(_FakeClient(), config_id="vision")

        ctrl = dispatcher._get_controller()

        assert ctrl.current == 2
        assert ctrl._min_c == 1
        assert ctrl._max_c == 5
        assert ctrl._window_size == 7
        assert ctrl._increase_cooldown == 3
        assert ctrl._latency_ceiling == 9000


class TestAIMDIncrease:
    @pytest.mark.asyncio
    async def test_stable_increases_by_one(self):
        ctrl = _make_controller(window_size=5, increase_cooldown=0)
        initial = ctrl.current
        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=50.0))
        assert ctrl.current == initial + 1

    @pytest.mark.asyncio
    async def test_does_not_exceed_max(self):
        ctrl = _make_controller(
            channel_count=1, initial_per_channel=24,
            max_per_channel=25, window_size=5, increase_cooldown=0,
        )
        assert ctrl.current == 24
        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=50.0))
        assert ctrl.current == 25
        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=50.0))
        assert ctrl.current == 25


class TestAIMDDecrease:
    @pytest.mark.asyncio
    async def test_rate_limit_triggers_multiplicative_decrease(self):
        ctrl = _make_controller(window_size=5)
        initial = ctrl.current
        for i in range(5):
            if i == 3:
                await ctrl.record(_make_metric(
                    success=False, is_rate_limited=True,
                ))
            else:
                await ctrl.record(_make_metric())
        expected = max(ctrl._min_c, int(initial * 0.7))
        assert ctrl.current == expected

    @pytest.mark.asyncio
    async def test_timeout_triggers_multiplicative_decrease(self):
        ctrl = _make_controller(window_size=5)
        initial = ctrl.current
        for i in range(5):
            if i == 4:
                await ctrl.record(_make_metric(
                    success=False, is_timeout=True,
                ))
            else:
                await ctrl.record(_make_metric())
        expected = max(ctrl._min_c, int(initial * 0.7))
        assert ctrl.current == expected

    @pytest.mark.asyncio
    async def test_consecutive_failures_trigger_heavy_decrease(self):
        ctrl = _make_controller(window_size=5)
        initial = ctrl.current
        for _ in range(2):
            await ctrl.record(_make_metric())
        for _ in range(3):
            await ctrl.record(_make_metric(
                success=False, is_rate_limited=True,
            ))
        expected = max(ctrl._min_c, int(initial * 0.5))
        assert ctrl.current == expected

    @pytest.mark.asyncio
    async def test_tail_latency_triggers_additive_decrease_when_switch_enabled(
        self, monkeypatch,
    ):
        """开关打开时 Level1 行为必须与历史一致（证明开关有效而非删码）"""
        monkeypatch.setattr(
            settings, "REVIEW_CONCURRENCY_TAIL_LATENCY_DECREASE_ENABLED", True,
        )
        ctrl = _make_controller(window_size=5)
        initial = ctrl.current
        for _ in range(3):
            await ctrl.record(_make_metric(latency_ms=100.0))
        for _ in range(2):
            await ctrl.record(_make_metric(latency_ms=400.0))
        assert ctrl.current == initial - 2
        assert ctrl.get_stats()["total_adjustments_down"] == 1

    @pytest.mark.asyncio
    async def test_tail_latency_does_not_decrease_by_default(self):
        """默认关闭尾延迟降档：同一序列不得降容，但信号必须仍被观测到"""
        ctrl = _make_controller(window_size=5)
        initial = ctrl.current
        for _ in range(3):
            await ctrl.record(_make_metric(latency_ms=100.0))
        for _ in range(2):
            await ctrl.record(_make_metric(latency_ms=400.0))
        stats = ctrl.get_stats()
        assert stats["total_adjustments_down"] == 0
        assert ctrl.current == initial + 1
        assert stats["tail_latency_decrease_enabled"] is False
        assert stats["tail_latency_observed"] == 1

    @pytest.mark.asyncio
    async def test_does_not_go_below_min(self):
        ctrl = _make_controller(
            channel_count=1, initial_per_channel=4,
            min_per_channel=3, window_size=5,
        )
        for _ in range(3):
            await ctrl.record(_make_metric(
                success=False, is_rate_limited=True,
            ))
        for _ in range(2):
            await ctrl.record(_make_metric())
        assert ctrl.current >= ctrl._min_c


class TestNaturalHeavyTail:
    """: 全成功的自然重尾不得独立把有效并发压到下限

 真实故障形态: 项目3221 的 207 条请求全部成功、无 429、无 timeout，
 有效并发仍从 20 棘轮式降到 5 且不再回升，Dispatcher 等待均值 8.2s -> 54.8s。
 以下用生产参数（5 通道 x 4 = 20 初始 / 下限 5 / 上限 40 / 窗口 15）复刻该形状。
 """

    @staticmethod
    def _production_shaped_controller() -> DispatcherConcurrencyController:
        return _make_controller(
            channel_count=5,
            initial_per_channel=4,
            min_per_channel=1,
            max_per_channel=8,
            window_size=15,
            increase_cooldown=0,
        )

    @pytest.mark.asyncio
    async def test_heavy_tail_does_not_ratchet_capacity_to_min(self):
        ctrl = self._production_shaped_controller()
        initial = ctrl.current
        assert (initial, ctrl._min_c, ctrl._max_c) == (20, 5, 40)

        for latency in _heavy_tail_latencies(120):
            await ctrl.record(_make_metric(latency_ms=latency))

        stats = ctrl.get_stats()
        assert stats["total_adjustments_down"] == 0
        assert ctrl.current > ctrl._min_c
        assert ctrl.current >= initial
        # 信号未被静默丢弃，仍可据此判断是否需要重新启用降档
        assert stats["tail_latency_observed"] > 0

    @pytest.mark.asyncio
    async def test_heavy_tail_collapses_to_min_when_switch_enabled(self, monkeypatch):
        """同一序列在开关打开时必须复现故障，证明差异来自开关而非夹具"""
        monkeypatch.setattr(
            settings, "REVIEW_CONCURRENCY_TAIL_LATENCY_DECREASE_ENABLED", True,
        )
        ctrl = self._production_shaped_controller()

        for latency in _heavy_tail_latencies(120):
            await ctrl.record(_make_metric(latency_ms=latency))

        assert ctrl.current == ctrl._min_c == 5
        assert ctrl.get_stats()["total_adjustments_down"] == 8

    @pytest.mark.asyncio
    async def test_capacity_recovers_after_rate_limit_despite_heavy_tail(self):
        """降容后遇到"重尾但健康"的窗口必须能回升

        增容守卫若继续用 p95(=窗口最大值)，同一条自然长尾会把回升永久冻结在 14。
        """
        ctrl = _make_controller(
            channel_count=1, initial_per_channel=20,
            min_per_channel=1, max_per_channel=25,
            window_size=5, increase_cooldown=0,
        )

        for i in range(5):
            await ctrl.record(_make_metric(
                latency_ms=5000.0, success=i != 3, is_rate_limited=i == 3,
            ))
        assert ctrl.current == 14

        # 每个满窗恰含一条 50s 长输出，其余为 5s 主体；无 429/timeout/失败
        for latency in (5000.0, 5000.0, 5000.0, 5000.0, 50000.0,
                        5000.0, 5000.0, 5000.0):
            await ctrl.record(_make_metric(latency_ms=latency))

        stats = ctrl.get_stats()
        assert ctrl.current == 18
        assert stats["total_adjustments_up"] == 4
        assert stats["tail_latency_observed"] == 4

    @pytest.mark.asyncio
    async def test_increase_still_blocked_when_median_latency_rises(self):
        """反向守卫: 中位数抬升是真实拥塞证据，即使降档关闭也不得继续增容"""
        ctrl = _make_controller(
            channel_count=1, initial_per_channel=10,
            min_per_channel=1, max_per_channel=25,
            window_size=5, increase_cooldown=0,
        )

        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=5000.0))
        assert ctrl.current == 11

        # 窗口被抬升后的延迟填满，中位数由 5s 升到 7s(+40%)，越过 1.3 倍守卫
        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=7000.0))
        settled = ctrl.current
        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=7000.0))

        assert settled == 13
        assert ctrl.current == settled
        assert ctrl.get_stats()["total_adjustments_up"] == 3

    @pytest.mark.asyncio
    async def test_increase_blocked_when_median_exceeds_latency_ceiling(self):
        ctrl = _make_controller(
            channel_count=1, initial_per_channel=10,
            min_per_channel=1, max_per_channel=25,
            window_size=5, increase_cooldown=0, latency_ceiling=60000,
        )

        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=90000.0))

        assert ctrl.current == 10
        assert ctrl.get_stats()["total_adjustments_up"] == 0


class TestSemaphoreResize:
    @pytest.mark.asyncio
    async def test_semaphore_value_increases(self):
        ctrl = _make_controller(window_size=5, increase_cooldown=0)
        initial_val = ctrl.semaphore._value
        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=50.0))
        assert ctrl.semaphore._value == initial_val + 1

    @pytest.mark.asyncio
    async def test_semaphore_value_decreases(self):
        ctrl = _make_controller(window_size=5)
        for i in range(5):
            if i == 3:
                await ctrl.record(_make_metric(
                    success=False, is_rate_limited=True,
                ))
            else:
                await ctrl.record(_make_metric())
        assert ctrl.semaphore._value < ctrl._initial

    @pytest.mark.asyncio
    async def test_saturated_decrease_applies_after_inflight_releases(self):
        ctrl = _make_controller(
            channel_count=1,
            initial_per_channel=20,
            min_per_channel=1,
            max_per_channel=25,
            window_size=5,
            increase_cooldown=0,
        )

        for _ in range(ctrl.current):
            await ctrl.semaphore.acquire()
        assert ctrl.semaphore._value == 0

        for i in range(5):
            await ctrl.record(_make_metric(
                success=i != 4,
                is_rate_limited=i == 4,
            ))

        assert ctrl.current == 14

        for _ in range(20):
            ctrl.semaphore.release()

        assert ctrl.semaphore._value == ctrl.current

        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=50.0))

        assert ctrl.current == 15
        assert ctrl.semaphore._value == ctrl.current


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_contains_expected_fields(self):
        ctrl = _make_controller()
        for _ in range(3):
            await ctrl.record(_make_metric())
        stats = ctrl.get_stats()
        assert "current" in stats
        assert "initial" in stats
        assert "min" in stats
        assert "max" in stats
        assert "p50_ms" in stats
        assert "p95_ms" in stats
        assert "window_fill" in stats
        assert "total_adjustments_up" in stats
        assert "total_adjustments_down" in stats

    @pytest.mark.asyncio
    async def test_adjustment_counters(self):
        ctrl = _make_controller(window_size=5, increase_cooldown=0)
        for _ in range(5):
            await ctrl.record(_make_metric(latency_ms=50.0))
        stats = ctrl.get_stats()
        assert stats["total_adjustments_up"] == 1
        assert stats["total_adjustments_down"] == 0


class TestWindowBehavior:
    @pytest.mark.asyncio
    async def test_no_adjustment_before_window_full(self):
        ctrl = _make_controller(window_size=5)
        initial = ctrl.current
        for _ in range(4):
            await ctrl.record(_make_metric(
                success=False, is_rate_limited=True,
            ))
        assert ctrl.current == initial

    @pytest.mark.asyncio
    async def test_window_clears_on_decrease(self):
        ctrl = _make_controller(window_size=5)
        for i in range(5):
            if i == 3:
                await ctrl.record(_make_metric(
                    success=False, is_rate_limited=True,
                ))
            else:
                await ctrl.record(_make_metric())
        assert ctrl._metrics.__len__() == 0
