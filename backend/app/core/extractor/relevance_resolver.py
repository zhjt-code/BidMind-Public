"""评审意义最终判定（P0/P1 共享解析器）。

职责: 组合"合同正文启发式 + 噪音规则护栏"，对单条要求做评审意义"降级"判定
      （actionable -> informational/procedural），供提取阶段与入库前共用。
不做: 不升级为 actionable、不调用 LLM、不访问 DB、不改写描述。

为何在入库前再判一次: 合并/去重阶段会重建 ExtractedRequirement，可能丢失 chunk
阶段打的降级标记；入库前基于最终描述用规则二次兜底，确保合同/噪音条款稳定折叠。
"""

from __future__ import annotations

from app.core.extractor.section_context import classify_section_phase
from app.core.extractor.tools.noise_classifier import classify_noise_relevance

_VALID_RELEVANCE = ("actionable", "informational", "procedural")


def resolve_review_relevance(
    current: str | None,
    description: str | None,
    keywords: list[str] | None = None,
    *,
    category: str | None = None,
    is_mandatory: bool = False,
    is_contract_section: bool = False,
) -> str:
    """判定一条要求的最终评审意义（仅降级，不升级）。

    Args:
        current: 当前级别（LLM 标注或 chunk 阶段结果）。
        description: 要求描述（入库前再判时的主要依据）。
        keywords: 关键词。
        category: 要求分类(QUAL/TECH/COMM/PERF/STAFF/FORMAT)，用于按类别噪音规则。
        is_mandatory: 是否否决项；否决项强制豁免，永不降级（防止跨类别折叠隐藏废标项）。
        is_contract_section: 是否来自合同区段（chunk 阶段已知时传入）。

    Returns:
        actionable / informational / procedural。
    """
    # 否决项豁免（最高优先级）：废标/实质性要求绝不折叠隐藏
    if is_mandatory:
        return "actionable"

    relevance = current if current in _VALID_RELEVANCE else "actionable"
    if relevance != "actionable":
        return relevance

    if is_contract_section:
        return "informational"
    if classify_section_phase(None, description).is_contract:
        return "informational"
    rule_level = classify_noise_relevance(description, keywords, category)
    if rule_level:
        return rule_level
    return "actionable"
