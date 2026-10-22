"""质量基线工具测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.vectorizer.quality_baseline import (
    build_quality_baseline_snapshot,
    compare_quality_snapshots,
)


def test_build_quality_baseline_snapshot() -> None:
    snapshot = build_quality_baseline_snapshot(
        total_items=10,
        missing_items=2,
        low_relevance_items=3,
        deep_search_items=4,
        source_page_hits=8,
    )
    assert snapshot.to_dict()["missing_response_ratio"] == 0.2
    assert snapshot.to_dict()["source_page_hit_ratio"] == 0.8


def test_compare_quality_snapshots_detects_degradation() -> None:
    baseline = build_quality_baseline_snapshot(
        total_items=10,
        missing_items=1,
        low_relevance_items=1,
        deep_search_items=2,
        source_page_hits=9,
    )
    candidate = build_quality_baseline_snapshot(
        total_items=10,
        missing_items=3,
        low_relevance_items=2,
        deep_search_items=2,
        source_page_hits=7,
    )

    diff = compare_quality_snapshots(baseline, candidate)
    assert diff["degraded"] is True
    assert diff["missing_response_delta"] > 0
    assert diff["source_page_hit_delta"] < 0
