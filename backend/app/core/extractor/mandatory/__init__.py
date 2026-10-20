"""
职责: 招标要求分块提取后、合并前的高风险原子条件召回入口。
不做: 不替代普通分块提取、不解析评分表、不审查投标响应、不落库。
"""

from app.core.extractor.mandatory.apply_coverage import recall_mandatory_conditions
from app.core.extractor.mandatory.signals import (
    atomic_conditions_must_stay_apart,
    looks_like_atomic_disqualifier,
)

__all__ = [
    "recall_mandatory_conditions",
    "atomic_conditions_must_stay_apart",
    "looks_like_atomic_disqualifier",
]
