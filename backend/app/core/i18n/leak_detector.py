"""用户可见文本内部术语泄漏检测器。

职责: 检测用户可见文本中残留的内部字段名/英文枚举/技术表达式，返回命中清单。
不做: 文本替换、映射表维护、业务判定、落库/序列化。

设计目标：
- 通杀赋值/比较表达式与「英文词紧邻页/分/项」等已知泄漏形态
- 字段名集合从 Pydantic schema 反射生成，新增字段自动纳入检测
- 供 sanitize 出口闸门调用：清洗后仍有命中则打 WARNING，使新形态可观测
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

# Prompt/Gate 常泄漏但不在 ExtractedReviewResult 上的字段
_EXTRA_LEAK_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "is_mandatory",
        "relevance_score",
        "compliance_level",
        "risk_level",
        "estimated_score",
        "evidence_anchor",
        "source_pages",
        "canonical_key",
        "fact_key",
        "rule_assessments",
        "golden_rule",
    }
)

# 审查类别英文枚举（RequirementCategory.value）
_CATEGORY_ENUMS: Final[frozenset[str]] = frozenset(
    {
        "qualification",
        "technical",
        "commercial",
        "performance",
        "staffing",
        "format",
    }
)

# 中文内部机制名 / 要求主键引用
_INTERNAL_ZH_MARKERS: Final[tuple[str, ...]] = (
    "项目证据账本",
    "来自要求#",
    "结论与账本冲突",
)

# 合法英文（资质标准号、常见缩写等）不视为泄漏
_ALLOWLIST_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"ISO\d*|GB/?T?\d*|CMMI|PMP|CISSP|ITIL|CE|CCC|CNAS|CMA|"
    r"PDF|Word|Excel|OCR|API|HTTP|HTTPS|URL|JSON|XML"
    r")\b",
    re.IGNORECASE,
)

# 字段赋值/比较表达式：is_mandatory=true / relevance_score<30 / score:100
_FIELD_EXPR_RE: Final[re.Pattern[str]] = re.compile(
    r"\b([a-z][a-z0-9_]{1,})\s*[=<>≤≥:：]+\s*[\w\"']+",
    re.IGNORECASE,
)

# snake_case 独立 token（含下划线）
_SNAKE_CASE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])([a-z][a-z0-9]*_[a-z0-9_]+)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

# 英文词紧邻「页/分/项」：technical页34 / performance页358
_EN_ADJACENT_CN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z]{3,})(?=页|分|项)"
)


@lru_cache(maxsize=1)
def _schema_field_names() -> frozenset[str]:
    """从审查相关 Pydantic schema 反射 snake_case 字段名集合。

    延迟 import 避免 i18n ↔ schemas 循环依赖；结果缓存。
    """
    names: set[str] = set(_EXTRA_LEAK_FIELDS)
    try:
        from app.schemas.review import ExtractedReviewResult, RuleAssessmentItem

        for model in (ExtractedReviewResult, RuleAssessmentItem):
            for field_name in model.model_fields:
                if "_" in field_name:
                    names.add(field_name.lower())
    except Exception:
        pass
    return frozenset(names)


def detect_internal_leaks(text: str | None) -> list[str]:
    """检测文本中残留的内部术语/技术表达式。

    Args:
        text: 待检测的用户可见文本

    Returns:
        命中片段列表（去重、保序）；无命中返回空列表。
    """
    if not text or not isinstance(text, str):
        return []

    scrubbed = _ALLOWLIST_RE.sub(" ", text)
    hits: list[str] = []
    seen: set[str] = set()

    def _add(fragment: str) -> None:
        key = fragment.lower().strip()
        if not key or key in seen:
            return
        seen.add(key)
        hits.append(fragment.strip())

    for match in _FIELD_EXPR_RE.finditer(scrubbed):
        _add(match.group(0))

    field_names = _schema_field_names()
    for match in _SNAKE_CASE_RE.finditer(scrubbed):
        token = match.group(1)
        if token.lower() in field_names or "_" in token:
            _add(token)

    for match in _EN_ADJACENT_CN_RE.finditer(scrubbed):
        word = match.group(1)
        if word.lower() in _CATEGORY_ENUMS or word.lower() in field_names:
            _add(f"{word}页/分/项")

    # 裸露的高风险字段名 / 类别枚举（无赋值符）
    for name in field_names | _CATEGORY_ENUMS:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        if pattern.search(scrubbed):
            _add(name)

    for marker in _INTERNAL_ZH_MARKERS:
        if marker in scrubbed:
            _add(marker)

    return hits
