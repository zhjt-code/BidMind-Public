"""评分档次带归并。

职责: 识别"主行满分 + 档次折扣行"版式, 把档次行归并为主行的评分规则,
 消除重复计分(如 3.0+2.4+2.1 三行同因素累加致盈余)。
不做: 不解析表格、不改卷归属、不做守恒裁决、不调用 LLM。

真实形态(项目2305): "项目管理大纲编制（3分）"主行后跟两行档次描述,
行分值 2.4/2.1 为主行满分的 80%/70% 折扣带, 且档次行无名称——
按条款号列派生出"2.2.4（1）"样式名称。
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from app.core.config import settings

# 条款号派生名模式: "2.2.4（1）" / "3.1(2)" 等——档次行无名称时按条款号列派生的特征
_DERIVED_NAME_PATTERN = re.compile(
    r"^\d+(?:\.\d+)*\s*[（(]\d+[）)]$"
)
# 档次折扣比区间: 常见 80%/70%(较好/一般档); 过低(<50%)多为独立小分项, 不归并
_BAND_RATIO_MIN = 0.5
_BAND_RATIO_MAX = 0.95


def _is_derived_band_name(name: str) -> bool:
    return bool(_DERIVED_NAME_PATTERN.match((name or "").strip()))


def _same_page(main: Any, candidate: Any) -> bool:
    main_pages = set(getattr(main, "source_pages", None) or [])
    cand_pages = set(getattr(candidate, "source_pages", None) or [])
    if not main_pages or not cand_pages:
        return True  # 页码缺失不作为否决证据(解析层可能未回填)
    return bool(main_pages & cand_pages)


def _is_grade_band_of(main: Any, candidate: Any) -> bool:
    """保守判定: 派生名 + 分值折扣带 + 同页, 三条件全部满足才认定档次行。"""
    if main is None:
        return False
    if not _is_derived_band_name(getattr(candidate, "score_item_name", "")):
        return False
    main_score = float(getattr(main, "score_weight", 0) or 0)
    cand_score = float(getattr(candidate, "score_weight", 0) or 0)
    if main_score <= 0 or cand_score <= 0 or cand_score >= main_score:
        return False
    ratio = cand_score / main_score
    if not (_BAND_RATIO_MIN <= ratio <= _BAND_RATIO_MAX):
        return False
    return _same_page(main, candidate)


def consolidate_grade_bands(items: list[Any]) -> tuple[list[Any], list[str]]:
    """归并档次带: 档次行文本并入主行 score_rules 并移除, 返回(新列表, 诊断)。

    主行=最近一个非派生名条目; 不满足任一判定条件的条目原样保留。
    """
    survivors: list[Any] = []
    merged: list[str] = []
    current_main: Any = None
    for item in items:
        if _is_grade_band_of(current_main, item):
            band_text = (
                f"档次带({item.score_weight:g}分): "
                + str(getattr(item, "description", "") or "").strip()[:200]
            )
            try:
                current_main.score_rules = list(current_main.score_rules) + [band_text]
            except (AttributeError, ValueError):
                pass
            merged.append(
                f"{current_main.score_item_name[:20]}<-{item.score_weight:g}分档次行"
            )
            continue
        if not _is_derived_band_name(getattr(item, "score_item_name", "")):
            current_main = item
        survivors.append(item)

    diagnostics: list[str] = []
    if merged:
        diagnostics.append(
            f"档次带归并: 合并{len(merged)}条折扣档次行进主行评分规则"
            f"({'; '.join(merged[:6])}{'...' if len(merged) > 6 else ''})"
        )
        logger.info("[评分档次带] {}", diagnostics[0])
    return survivors, diagnostics


def consolidate_grade_bands_if_enabled(
    items: list[Any],
) -> tuple[list[Any], list[str]]:
    """开关封装(rescue tail 薄委托点): 关闭时零改动返回原列表。"""
    if not bool(getattr(settings, "SCORING_GRADE_BAND_CONSOLIDATION_ENABLED", True)):
        return items, []
    if not items:
        return items, []
    return consolidate_grade_bands(items)


__all__ = ["consolidate_grade_bands", "consolidate_grade_bands_if_enabled"]
