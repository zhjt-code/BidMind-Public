"""评分结构判读器(SchemeReader)子模块。

职责: 消费评分结构信号快照(documents.scoring_signals), 用 LLM 判读评分方案/卷
      边界(SchemeMap), 经锚点核验后供明细贴标与逐卷对账使用。
不做: 不解析表格明细、不产chunk、不落库、不做守恒最终裁决(scope_validator 职责)。
"""

from app.core.extractor.scoring.scheme_reader.contracts import (
    SchemeMap,
    SchemeMapReading,
    SignalAnchor,
    SpanAssignment,
)
from app.core.extractor.scoring.scheme_reader.reader import read_scheme_map

__all__ = [
    "SchemeMap",
    "SchemeMapReading",
    "SignalAnchor",
    "SpanAssignment",
    "read_scheme_map",
]
