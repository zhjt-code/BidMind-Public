"""章节画像质量与分批辅助。

职责: 提供章节画像质量单一权威源(SSOT)与叶子分批纯函数，供画像生成与匹配管线共用。
不做: 不调用 LLM、不写库、不编排生成流程。
"""

from app.core.writer.chapter_profiling.batching import (
    compute_coverage,
    find_missing_ids,
    split_leaves_into_batches,
)
from app.core.writer.chapter_profiling.quality import (
    ProfileQuality,
    assess_profile_data,
    assess_profile_raw,
    is_matchable,
)

__all__ = [
    "ProfileQuality",
    "assess_profile_data",
    "assess_profile_raw",
    "is_matchable",
    "split_leaves_into_batches",
    "compute_coverage",
    "find_missing_ids",
]
