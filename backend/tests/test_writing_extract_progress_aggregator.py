# -*- coding: utf-8 -*-
"""写标约束提取三层并行进度聚合防御测试。

真实事件序列取自现网日志: hard/structure/guide 三层通过 asyncio.gather 并行,
每层各自维护 extracted_count 并互相打入同一心跳通道, 导致 heartbeat 的
overall% 在层间跳跃。本测试锁定聚合器契约, 阻断该回归。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.writer.clause_extract_progress import (  # noqa: E402
    ClauseExtractProgressAggregator,
    build_writing_extract_progress_callback,
)


def test_first_event_locks_total_and_computes_pct():
    agg = ClauseExtractProgressAggregator()
    snap = agg.record(layer="hard_constraint", done=1, total_per_layer=10)
    # 三层占 pipeline 0-60；1/30 × 60 = 2
    assert snap.overall_pct == 2
    assert "1/30" in snap.message


def test_three_layers_interleaved_stay_monotonic():
    """现网复现: hard/guide/structure 交替上报，聚合结果必须严格单调。"""
    agg = ClauseExtractProgressAggregator()
    events = [
        ("hard_constraint", 5),
        ("generation_guide", 8),
        ("structure_constraint", 6),
        ("hard_constraint", 16),
        ("generation_guide", 20),
        ("hard_constraint", 21),
        ("structure_constraint", 30),
        ("generation_guide", 45),
        ("structure_constraint", 55),
    ]
    prev = 0
    for layer, done in events:
        snap = agg.record(layer=layer, done=done, total_per_layer=62)
        assert snap.overall_pct >= prev, (
            f"聚合后进度回退: {layer}={done} prev={prev} now={snap.overall_pct}"
        )
        prev = snap.overall_pct


def test_intra_layer_regression_is_clamped():
    """同层 done 回退（例如重试路径重新计数）也必须钳制单调。"""
    agg = ClauseExtractProgressAggregator()
    first = agg.record(layer="hard_constraint", done=10, total_per_layer=20)
    second = agg.record(layer="hard_constraint", done=5, total_per_layer=20)
    # 层内 done 被钳制到 10, 整体应保持不动
    assert second.overall_pct == first.overall_pct
    assert "10/60" in second.message


def test_three_layers_complete_reaches_sixty_percent():
    agg = ClauseExtractProgressAggregator()
    for layer in ("hard_constraint", "structure_constraint", "generation_guide"):
        agg.record(layer=layer, done=10, total_per_layer=10)
    snap = agg.snapshot()
    assert snap.overall_pct == 60
    assert "30/30" in snap.message


def test_unknown_layer_rejected():
    agg = ClauseExtractProgressAggregator()
    with pytest.raises(ValueError):
        agg.record(layer="unknown_layer", done=1, total_per_layer=10)


def test_total_zero_is_safe():
    """未知总数或空文档时不得除零。"""
    agg = ClauseExtractProgressAggregator()
    snap = agg.record(layer="hard_constraint", done=0, total_per_layer=0)
    assert snap.overall_pct == 0
    assert snap.message == ""


def test_callback_layer_events_unify_to_extracting_stage_and_stay_monotonic():
    """集成: 三层 stage 事件全部改写为统一的 extracting stage，且百分比单调。"""
    hb_calls: list[tuple[str, str]] = []

    def fake_hb(name, *, stage="", progress="", extra=None):  # noqa: ANN001
        hb_calls.append((stage, progress))

    callback = build_writing_extract_progress_callback(
        task_name="writing_extract_100",
        heartbeat=fake_hb,
    )

    callback("extracting_hard_constraint", 5, 10, "hard_constraint: 5/10")
    callback("extracting_generation_guide", 8, 10, "generation_guide: 8/10")
    callback("extracting_structure_constraint", 6, 10, "structure_constraint: 6/10")
    callback("extracting_hard_constraint", 6, 10, "hard_constraint: 6/10")

    layer_events = [call for call in hb_calls if call[0] == "extracting"]
    assert len(layer_events) == 4, "每次层事件都应产生 extracting stage 心跳"
    pcts = [int(prog.split("%", 1)[0]) for _, prog in layer_events]
    assert pcts == sorted(pcts), f"心跳百分比出现回退: {pcts}"

    # 三层 stage 不得再直接暴露给心跳（避免前端 stage 判定错乱）
    assert not any(
        stage.startswith("extracting_hard_constraint")
        or stage.startswith("extracting_structure_constraint")
        or stage.startswith("extracting_generation_guide")
        for stage, _ in hb_calls
    ), "三层子 stage 必须被聚合为 extracting"


def test_single_pass_uses_batch_total_not_times_three():
    """: 单次过正文分母是批次数，不再 ×3。"""
    agg = ClauseExtractProgressAggregator()
    snap = agg.record(layer="clauses", done=1, total_per_layer=10)
    assert snap.overall_pct == 6
    assert "1/10" in snap.message
    assert "1/30" not in snap.message

    done = agg.record(layer="clauses", done=10, total_per_layer=10)
    assert done.overall_pct == 60
    assert "10/10" in done.message


def test_callback_single_pass_stage_unifies_to_extracting():
    hb_calls: list[tuple[str, str]] = []

    def fake_hb(name, *, stage="", progress="", extra=None):  # noqa: ANN001
        hb_calls.append((stage, progress))

    callback = build_writing_extract_progress_callback(
        task_name="writing_extract_100",
        heartbeat=fake_hb,
    )
    callback("extracting_clauses", 5, 10, "约束提取 5/10")
    assert hb_calls[-1][0] == "extracting"
    assert "5/10" in hb_calls[-1][1]
    assert "5/30" not in hb_calls[-1][1]


def test_callback_non_layer_stages_bypass_aggregator():
    """dedup / saving / render_profile 等顺序 stage 保持原权重计算。"""
    hb_calls: list[tuple[str, str]] = []

    def fake_hb(name, *, stage="", progress="", extra=None):  # noqa: ANN001
        hb_calls.append((stage, progress))

    callback = build_writing_extract_progress_callback(
        task_name="writing_extract_100",
        heartbeat=fake_hb,
    )

    callback("dedup", 0, 3, "去重")
    callback("saving", 30, 100, "保存")

    stages = {stage for stage, _ in hb_calls}
    assert "dedup" in stages
    assert "saving" in stages
