"""评分项结构身份键。

职责: 基于方案、卷、来源页、满分和评分对象名生成稳定去重键。
不做: 不判断是否同一评分项、不修改分值、不调用 LLM/DB。
"""

from __future__ import annotations

import re
from typing import Any


def build_scoring_identity(item: Any) -> tuple[str, str, str, float, str] | None:
    """为评分项构建结构身份键；关键字段不足时返回 None。

 scheme 分量: 不同评分方案的同名同分项是不同事实, 精确去重不得跨方案命中。
 """
    scheme = str(getattr(item, "scheme", "") or "__single__")
    volume = str(getattr(item, "volume", "") or "unspecified")
    name = _normalize_name(getattr(item, "score_item_name", "") or "")
    score = _normalize_score(getattr(item, "score_weight", None))
    if not name or score is None:
        return None
    return (scheme, volume, _page_key(getattr(item, "source_pages", None)), score, name)


def _normalize_name(name: str) -> str:
    normalized = re.sub(r"[\s:：;；,，。]", "", str(name or ""))
    return normalized if len(normalized) > 1 else ""


def _normalize_score(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return round(value, 4)


# 身份键碰撞时判定"确为同一物理行"的描述近乎一致阈值:
# 归一名可能是命名净化的派生名, 派生错误会让不同物理行身份键碰撞;
# 真正的解析重复行(同表重复副本)描述必然近乎一致。
_IDENTITY_MERGE_DESC_THRESHOLD = 0.85


def is_identity_merge_safe(kept: Any, incoming: Any) -> bool:
    """身份键碰撞的两行是否确为同一物理行(可安全合并)。

 物理行守恒护栏: 描述并非近乎一致时禁止合并——命名污染只能
 降级为"名字重复", 不得升级为"评分事实丢失"(评分事实守恒红线)。
 """
    from app.core.extractor.scoring.scoring_reconcile import bigram_similarity

    return bigram_similarity(
        getattr(kept, "description", None), getattr(incoming, "description", None)
    ) >= _IDENTITY_MERGE_DESC_THRESHOLD


def _page_key(raw_pages: Any) -> str:
    pages: list[int] = []
    if isinstance(raw_pages, list):
        for page in raw_pages:
            try:
                pages.append(int(page))
            except (TypeError, ValueError):
                continue
    pages = sorted(set(p for p in pages if p > 0))
    if not pages:
        return "unknown"
    if pages == list(range(pages[0], pages[-1] + 1)):
        return f"{pages[0]}-{pages[-1]}"
    return ",".join(str(p) for p in pages)


__all__ = ["build_scoring_identity", "is_identity_merge_safe"]
