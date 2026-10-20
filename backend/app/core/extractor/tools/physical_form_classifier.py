"""B4 确定性算法：物理形态 / 程序性要求分类器。

纯函数实现，严禁网络 / DB 调用。
YAML 词典在模块导入时加载一次，后续复用缓存对象。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

# ---------------------------------------------------------------------------
# 模块级缓存：YAML 仅加载一次
# ---------------------------------------------------------------------------
_DOMAIN_TERMS: dict | None = None


def load_domain_terms() -> dict:
    """加载并缓存 domain_terms.yaml。

    Returns:
        解析后的词典数据，结构与 domain_terms.yaml 完全对应。
    """
    global _DOMAIN_TERMS
    if _DOMAIN_TERMS is None:
        yaml_path = Path(__file__).parent / "domain_terms.yaml"
        with yaml_path.open(encoding="utf-8") as fh:
            _DOMAIN_TERMS = yaml.safe_load(fh)
    return _DOMAIN_TERMS


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhysicalFormVerdict:
    """物理形态 / 程序性要求分类结果。

    Attributes:
        is_physical_form: 文本包含物理形态要求关键词。
        is_procedural:    文本包含程序性要求关键词。
        matched_keywords: 命中的关键词列表（物理形态 + 程序性合并）。
        negations:        在关键词前 10 字符内检测到的否定词列表。
        confidence:       "high" 表示有效命中且无否定；"low" 表示命中但存在否定，
                          或完全无匹配（无匹配时 is_physical_form/is_procedural
                          均为 False，confidence 仍为 "high"）。
    """
    is_physical_form: bool
    is_procedural: bool
    matched_keywords: list[str] = field(default_factory=list)
    negations: list[str] = field(default_factory=list)
    confidence: Literal["high", "low"] = "low"


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _find_negations_for_keyword(text: str, keyword: str, negation_words: list[str]) -> list[str]:
    """检测 keyword 前 10 字符内是否存在否定词。

    Args:
        text:           被检测的原始文本。
        keyword:        已确认存在于 text 中的关键词。
        negation_words: 候选否定词列表。

    Returns:
        在该 keyword 前 10 字符窗口内出现的否定词列表（可能为空）。
    """
    found: list[str] = []
    start = 0
    while True:
        pos = text.find(keyword, start)
        if pos == -1:
            break
        # 取关键词起始位置前 10 个字符作为窗口
        window_start = max(0, pos - 10)
        window = text[window_start:pos]
        for neg in negation_words:
            if neg in window and neg not in found:
                found.append(neg)
        start = pos + 1
    return found


# ---------------------------------------------------------------------------
# 核心公开 API
# ---------------------------------------------------------------------------

def classify_requirement(requirement_text: str) -> PhysicalFormVerdict:
    """将一条招标要求文本分类为物理形态要求、程序性要求，或两者皆非。

    分类逻辑（按顺序）:
    1. 在 ``physical_form.keywords`` 中查找命中词 → is_physical_form。
    2. 在 ``physical_form.procedural_keywords`` 中查找命中词 → is_procedural。
    3. 对每个命中词，检测其前 10 字符内是否含否定词。
    4. 有命中且无否定 → confidence="high"。
       有命中但存在否定 → confidence="low"。
       无任何命中 → is_physical_form=False, is_procedural=False, confidence="high"。

    Args:
        requirement_text: 待分类的招标要求文本（中文）。

    Returns:
        :class:`PhysicalFormVerdict` 实例，包含分类结果与置信度。
    """
    terms = load_domain_terms()
    pf_section: dict = terms.get("physical_form", {})
    kw_list: list[str] = pf_section.get("keywords", [])
    proc_kw_list: list[str] = pf_section.get("procedural_keywords", [])
    neg_words: list[str] = pf_section.get("negation_words", [])

    matched_physical: list[str] = [kw for kw in kw_list if kw in requirement_text]
    matched_procedural: list[str] = [kw for kw in proc_kw_list if kw in requirement_text]

    all_matched: list[str] = matched_physical + matched_procedural

    if not all_matched:
        return PhysicalFormVerdict(
            is_physical_form=False,
            is_procedural=False,
            matched_keywords=[],
            negations=[],
            confidence="high",
        )

    # 收集所有命中词对应的否定词（去重）
    found_negations: list[str] = []
    for kw in all_matched:
        neg_hits = _find_negations_for_keyword(requirement_text, kw, neg_words)
        for neg in neg_hits:
            if neg not in found_negations:
                found_negations.append(neg)

    confidence: Literal["high", "low"] = "low" if found_negations else "high"

    return PhysicalFormVerdict(
        is_physical_form=bool(matched_physical),
        is_procedural=bool(matched_procedural),
        matched_keywords=all_matched,
        negations=found_negations,
        confidence=confidence,
    )


def substance_keyword_hits(requirement_text: str) -> list[str]:
    """命中实质审查领域词，用于禁止复合条款算法放行。

    词典来自 domain_terms.yaml 的 relevance_domains（业绩/资质/人员/商务/技术）。
    空文本返回空列表。
    """
    if not requirement_text:
        return []
    terms = load_domain_terms()
    hits: list[str] = []
    domains = terms.get("relevance_domains") or {}
    for section in domains.values():
        if not isinstance(section, dict):
            continue
        for kw in section.get("keywords") or []:
            if kw and kw in requirement_text and kw not in hits:
                hits.append(kw)
    return hits
