"""章节画像质量单一权威源(SSOT)。

职责: 对单条章节画像做三态判定(matchable / complete / degraded)，统一生成侧与匹配侧口径。
不做: 不调用 LLM、不写库、不修改画像内容、不决定是否补救。

三态约定:
- matchable: semantic_role 非空且 expects>=1 —— 可进入匹配管线
- complete: matchable 且 rejects>=2 —— 理想完整画像
- degraded: 非 matchable —— 禁止标题-only 静默降级，下游需旁证或显式降级
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Optional


class ProfileQuality(str, Enum):
    """章节画像质量三态。"""

    MATCHABLE = "matchable"
    COMPLETE = "complete"
    DEGRADED = "degraded"


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def assess_profile_data(data: Optional[dict[str, Any]]) -> ProfileQuality:
    """对已解析的画像 dict 做三态判定。

    Args:
        data: 画像字段 dict，期望含 semantic_role / expects / rejects。

    Returns:
        ProfileQuality 三态之一。
    """
    if not isinstance(data, dict):
        return ProfileQuality.DEGRADED

    role = str(data.get("semantic_role") or "").strip()
    expects = _as_str_list(data.get("expects"))
    rejects = _as_str_list(data.get("rejects"))

    if not role or len(expects) < 1:
        return ProfileQuality.DEGRADED
    if len(rejects) >= 2:
        return ProfileQuality.COMPLETE
    return ProfileQuality.MATCHABLE


def assess_profile_raw(raw: Any) -> ProfileQuality:
    """对落库原始 chapter_profile(JSON 文本或 dict)做三态判定。"""
    if not raw:
        return ProfileQuality.DEGRADED
    if isinstance(raw, dict):
        return assess_profile_data(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ProfileQuality.DEGRADED
        return assess_profile_data(parsed if isinstance(parsed, dict) else None)
    return ProfileQuality.DEGRADED


def is_matchable(quality: ProfileQuality) -> bool:
    """matchable 或 complete 均可进入匹配；degraded 不可。"""
    return quality in (ProfileQuality.MATCHABLE, ProfileQuality.COMPLETE)


__all__ = [
    "ProfileQuality",
    "assess_profile_data",
    "assess_profile_raw",
    "is_matchable",
]
