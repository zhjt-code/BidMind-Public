"""评分项提取子包（P2 评分表聚焦）。

职责: 从评分表整表 chunk 提取结构化评分项并持久化为 scoring_item 类型的招标要求。
不做: 不做普通招标要求提取（见 tender_extractor）、不做表格拼接（见 parser.table_stitcher）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.extractor.scoring.score_totals import (
    ScoreTotalsReport,
    verify_score_totals,
)

if TYPE_CHECKING:
    from app.core.extractor.scoring.scoring_extractor import ScoringExtractor


def get_scoring_extractor(*args, **kwargs):
    """懒加载评分项提取器，避免 parser 与 extractor 包初始化形成循环导入。"""
    from app.core.extractor.scoring.scoring_extractor import get_scoring_extractor as _factory

    return _factory(*args, **kwargs)


def __getattr__(name: str):
    if name == "ScoringExtractor":
        from app.core.extractor.scoring.scoring_extractor import ScoringExtractor

        return ScoringExtractor
    raise AttributeError(name)


__all__ = [
    "ScoringExtractor",
    "get_scoring_extractor",
    "ScoreTotalsReport",
    "verify_score_totals",
]
