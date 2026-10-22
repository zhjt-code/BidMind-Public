"""写标约束消费作用域策略

职责: 统一读取/写入 writing_relevance 元数据, 判定约束是否应计入正文覆盖与匹配消费。
不做: 不访问 DB/LLM、不做章节匹配、不做去重、不修改传入对象。
"""

from __future__ import annotations

from typing import Any

WRITING_RELEVANCE_DIRECT = "direct"
WRITING_RELEVANCE_REFERENCE = "reference"
WRITING_RELEVANCE_CHECK_ONLY = "check_only"
WRITING_RELEVANCE_MANUAL_SOURCE = "manual"

# 不计入"应进正文来源"覆盖分母/匹配池的旁路消费 sub_type 单一权威来源。
# submission_rule / submission_notice 由 render_profile 与素材页注意事项清单消费。
HIDDEN_COVERAGE_SUB_TYPES = frozenset({
    "bid_structure_analysis",
    "bid_directory_spec",
    "render_profile",
    "industry_profile",
    "submission_notice",
    "submission_rule",
})

_NON_BODY_RELEVANCE = {
    WRITING_RELEVANCE_REFERENCE,
    WRITING_RELEVANCE_CHECK_ONLY,
}

_GUIDE_FALLBACK_SUB_TYPES = {
    "emphasis_point",
    "differentiation_hint",
}


def _metadata(clause: Any) -> dict[str, Any]:
    raw = getattr(clause, "metadata_json", None)
    return raw if isinstance(raw, dict) else {}


def is_manual_relevance_override(clause: Any) -> bool:
    """人工来源的 writing_relevance 视为显式恢复正文消费。"""
    return _metadata(clause).get("writing_relevance_source") == WRITING_RELEVANCE_MANUAL_SOURCE


def read_writing_relevance(clause: Any) -> str:
    """读取约束写作消费级别; 人工覆盖统一按 direct 进入正文消费。"""
    if is_manual_relevance_override(clause):
        return WRITING_RELEVANCE_DIRECT
    value = _metadata(clause).get("writing_relevance")
    if value in {
        WRITING_RELEVANCE_DIRECT,
        WRITING_RELEVANCE_REFERENCE,
        WRITING_RELEVANCE_CHECK_ONLY,
    }:
        return str(value)
    return WRITING_RELEVANCE_DIRECT


def is_body_consumable_clause(clause: Any) -> bool:
    """是否允许进入正文匹配/生成消费池。"""
    sub_type = getattr(clause, "sub_type", "")
    if sub_type in HIDDEN_COVERAGE_SUB_TYPES:
        return False
    return read_writing_relevance(clause) not in _NON_BODY_RELEVANCE


def is_body_coverage_counted(clause: Any) -> bool:
    """是否计入已确认约束的正文覆盖分母。"""
    if int(getattr(clause, "confirmed", 0) or 0) != 1:
        return False
    return is_body_consumable_clause(clause)


def build_relevance_mark(
    metadata: dict[str, Any] | None,
    *,
    level: str,
    source: str,
    reason: str,
) -> dict[str, Any]:
    """返回带 writing_relevance 标记的新 metadata dict。"""
    merged = dict(metadata or {})
    merged["writing_relevance"] = level
    merged["writing_relevance_source"] = source
    merged["writing_relevance_reason"] = reason
    return merged


def is_writing_guide_fallback_clause(clause: Any) -> bool:
    """写作引导类未命中章节时可作为全局 highlight 指令兜底消费。"""
    return (
        getattr(clause, "sub_type", "") in _GUIDE_FALLBACK_SUB_TYPES
        and is_body_consumable_clause(clause)
    )


__all__ = [
    "WRITING_RELEVANCE_CHECK_ONLY",
    "WRITING_RELEVANCE_DIRECT",
    "HIDDEN_COVERAGE_SUB_TYPES",
    "WRITING_RELEVANCE_MANUAL_SOURCE",
    "WRITING_RELEVANCE_REFERENCE",
    "build_relevance_mark",
    "is_body_consumable_clause",
    "is_body_coverage_counted",
    "is_manual_relevance_override",
    "is_writing_guide_fallback_clause",
    "read_writing_relevance",
]
