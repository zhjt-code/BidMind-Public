"""招标-投标匹配模块

本模块负责将招标要求与投标响应进行关联匹配。

流程定位：
    招标要求（已确认）  →
                         【匹配模块】 → 匹配对列表 → AI对比审查
    投标响应（已提取）  →

匹配策略：
1. 基于requirement_id的直接关联（投标响应已包含requirement_id）
2. 补充语义匹配（处理遗漏情况）
"""

from app.core.matcher.requirement_matcher import (
    MatchedPair,
    RequirementMatcher,
    get_requirement_matcher,
)

__all__ = [
    "RequirementMatcher",
    "MatchedPair",
    "get_requirement_matcher",
]
