"""P1.3 防御测试：DegradationAggregator 任务级聚合行为。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.observability.degradation_aggregator import (
    DegradationAggregator,
    clear_aggregator,
    get_aggregator,
    record_degradation,
    set_aggregator_for_task,
    set_denominator,
)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    clear_aggregator()


class TestAggregatorLifecycle:

    def test_record_without_aggregator_silently_ignored(self):
        clear_aggregator()
        # 不 set_aggregator_for_task 直接 record, 不应抛异常
        record_degradation("material_tag_failed", ref="1", reason="x")
        assert get_aggregator() is None

    def test_set_creates_bound_instance(self):
        agg = set_aggregator_for_task("writer_compose")
        assert get_aggregator() is agg
        assert agg.flow == "writer_compose"

    def test_record_after_set_populates_entries(self):
        set_aggregator_for_task("writer_compose")
        record_degradation("material_tag_failed", ref="101", reason="timeout")
        record_degradation("chapter_judge_failed", ref="ch_1", reason="provider err")
        agg = get_aggregator()
        assert agg is not None
        entries = agg.entries()
        assert len(entries) == 2
        assert entries[0].kind == "material_tag_failed"
        assert entries[1].kind == "chapter_judge_failed"


class TestSummarize:

    def test_below_threshold_not_severe(self):
        set_aggregator_for_task("writer_compose")
        set_denominator("material_tag_failed", 50)
        # 5/50 = 10% < 20% 阈值, 不视为严重
        for i in range(5):
            record_degradation("material_tag_failed", ref=str(i))
        agg = get_aggregator()
        summary = agg.summarize()
        assert summary.counts_by_kind["material_tag_failed"] == 5
        assert "material_tag_failed" not in summary.severe_kinds
        assert "素材打标失败 5/50" in summary.text

    def test_above_threshold_is_severe(self):
        set_aggregator_for_task("writer_compose")
        set_denominator("material_tag_failed", 10)
        # 3/10 = 30% > 20% 阈值, 判定为严重
        for i in range(3):
            record_degradation("material_tag_failed", ref=str(i))
        agg = get_aggregator()
        summary = agg.summarize()
        assert "material_tag_failed" in summary.severe_kinds

    def test_small_denominator_not_severe(self):
        set_aggregator_for_task("writer_compose")
        set_denominator("material_tag_failed", 2)  # 小于 min_denom=3
        record_degradation("material_tag_failed", ref="1")
        agg = get_aggregator()
        summary = agg.summarize()
        # 分母太小不判严重 (避免小规模误判)
        assert "material_tag_failed" not in summary.severe_kinds

    def test_cross_reference_single_failure_not_severe(self):
        """cross_reference 本就只调用一次, 单次失败不应触发 severe。"""
        set_aggregator_for_task("writer_compose")
        set_denominator("cross_reference_llm_failed", 1)
        record_degradation("cross_reference_llm_failed", ref="cross_ref")
        summary = get_aggregator().summarize()
        # 阈值 1.0 (即 100%) 故 ratio > 1.0 才 severe, 单次失败 ratio=1.0 不大于 1.0
        assert "cross_reference_llm_failed" not in summary.severe_kinds

    def test_compose_failed_severe(self):
        set_aggregator_for_task("writer_compose")
        set_denominator("compose_failed", 20)
        # compose_failed 阈值 10%: 3/20=15%>10%
        for i in range(3):
            record_degradation("compose_failed", ref=f"node_{i}")
        summary = get_aggregator().summarize()
        assert "compose_failed" in summary.severe_kinds

    def test_empty_aggregator(self):
        set_aggregator_for_task("writer_compose")
        summary = get_aggregator().summarize()
        assert summary.text == ""
        assert summary.severe_kinds == []


class TestMultipleKindsMixed:

    def test_mixed_kinds_aggregated(self):
        set_aggregator_for_task("writer_compose")
        set_denominator("material_tag_failed", 50)
        set_denominator("compose_failed", 20)
        record_degradation("material_tag_failed", ref="1")
        record_degradation("material_tag_failed", ref="2")
        record_degradation("compose_failed", ref="node_1")
        summary = get_aggregator().summarize()
        assert summary.counts_by_kind == {
            "material_tag_failed": 2,
            "compose_failed": 1,
        }
        assert "素材打标失败 2/50" in summary.text
        assert "章节生成失败 1/20" in summary.text
