"""向量化质量基线与影子对比工具。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityBaselineSnapshot:
    """质量基线快照。"""

    missing_response_ratio: float
    low_relevance_ratio: float
    deep_search_ratio: float
    source_page_hit_ratio: float

    def to_dict(self) -> dict[str, float]:
        return {
            "missing_response_ratio": round(self.missing_response_ratio, 6),
            "low_relevance_ratio": round(self.low_relevance_ratio, 6),
            "deep_search_ratio": round(self.deep_search_ratio, 6),
            "source_page_hit_ratio": round(self.source_page_hit_ratio, 6),
        }


def build_quality_baseline_snapshot(
    *,
    total_items: int,
    missing_items: int,
    low_relevance_items: int,
    deep_search_items: int,
    source_page_hits: int,
) -> QualityBaselineSnapshot:
    """根据抽样统计构建质量基线。"""
    denom = max(total_items, 1)
    return QualityBaselineSnapshot(
        missing_response_ratio=missing_items / denom,
        low_relevance_ratio=low_relevance_items / denom,
        deep_search_ratio=deep_search_items / denom,
        source_page_hit_ratio=source_page_hits / denom,
    )


def compare_quality_snapshots(
    baseline: QualityBaselineSnapshot,
    candidate: QualityBaselineSnapshot,
) -> dict[str, float | bool]:
    """比较新旧快照，生成差异摘要。"""
    deltas = {
        "missing_response_delta": candidate.missing_response_ratio - baseline.missing_response_ratio,
        "low_relevance_delta": candidate.low_relevance_ratio - baseline.low_relevance_ratio,
        "deep_search_delta": candidate.deep_search_ratio - baseline.deep_search_ratio,
        "source_page_hit_delta": candidate.source_page_hit_ratio - baseline.source_page_hit_ratio,
    }
    degraded = (
        deltas["missing_response_delta"] > 0
        or deltas["low_relevance_delta"] > 0
        or deltas["source_page_hit_delta"] < 0
    )
    return {
        **{key: round(value, 6) for key, value in deltas.items()},
        "degraded": degraded,
    }
