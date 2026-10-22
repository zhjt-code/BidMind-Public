"""
职责: 把作用域原子条件与已提取要求对齐，决定抬标、补插与未认领信号。
不做: 不调用 LLM、不访问 DB、不解析评分表。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.extractor.mandatory.contracts import (
    AtomicCondition,
    CoverageDecision,
    ExistingRequirementView,
    MandatorySignal,
    PageText,
    ScopeWindow,
)
from app.core.extractor.mandatory.requirement_voice import (
    covers_same_condition,
    normalized_covers,
)
from app.core.extractor.mandatory.signals import (
    list_enumerable_anchors,
    looks_like_atomic_disqualifier,
)

_REVIEWABLE_KINDS = frozenset({"normal", ""})


def text_covers(haystack: str, needle: str) -> bool:
    """归一化后的包含或双字重叠，用于认领原子条件/信号。"""
    return normalized_covers(haystack, needle)


def decide_mandatory_coverage(
    *,
    pages: Sequence[PageText],
    windows: Sequence[ScopeWindow],
    signals: Sequence[MandatorySignal],
    existing: Sequence[ExistingRequirementView],
    extracted_atomics: Sequence[AtomicCondition],
) -> CoverageDecision:
    """根据已有要求和召回原子条件计算覆盖决策。"""
    reviewable = [
        item
        for item in existing
        if (item.requirement_kind or "normal") in _REVIEWABLE_KINDS
    ]
    promote = {
        item.index
        for item in reviewable
        if not item.is_mandatory and looks_like_atomic_disqualifier(item.description)
    }
    claimed_sources = list(reviewable)
    inserts: list[AtomicCondition] = []
    for atomic in extracted_atomics:
        desc = (atomic.description or "").strip()
        if len(desc) < 8:
            continue
        covered = [
            item
            for item in claimed_sources
            if covers_same_condition(item.description, desc)
        ]
        if covered:
            for item in covered:
                if not item.is_mandatory:
                    promote.add(item.index)
            continue
        if any(covers_same_condition(other.description, desc) for other in inserts):
            continue
        inserts.append(atomic)

    claimants = [item.description for item in claimed_sources] + [
        item.description for item in inserts
    ]
    unclaimed: list[MandatorySignal] = []
    signal_used: set[int] = set()
    for signal in signals:
        if _signal_claimed(signal, claimants, signal_used):
            continue
        unclaimed.append(signal)

    missing_anchors = 0
    window_used: set[int] = set()
    for window in windows:
        for anchor in list_enumerable_anchors(pages, window):
            if not _take_claimant(claimants, window_used, anchor):
                missing_anchors += 1

    warnings = _build_warnings(unclaimed, missing_anchors, len(promote), len(inserts))
    return CoverageDecision(
        promote_indices=sorted(promote),
        inserts=inserts,
        unclaimed=unclaimed,
        missing_anchor_count=missing_anchors,
        warnings=warnings,
    )


def _take_claimant(claimants: Sequence[str], used: set[int], candidate: str) -> bool:
    """
    职责: 按条件核对候选做一对一认领并消耗认领方。
    不做: 不看页码、不按废标短语计数、不调用 LLM、不合并条目。
    """
    for index, text in enumerate(claimants):
        if index in used:
            continue
        if covers_same_condition(text, candidate):
            used.add(index)
            return True
    return False


def _signal_claimed(
    signal: MandatorySignal,
    claimants: Sequence[str],
    used: set[int],
) -> bool:
    if signal.kind == "consequence_lead":
        return True
    return _take_claimant(claimants, used, signal.snippet)


def _build_warnings(
    unclaimed: Sequence[MandatorySignal],
    missing_anchors: int,
    promoted: int,
    inserted: int,
) -> list[str]:
    warnings: list[str] = []
    if unclaimed:
        pages = sorted({signal.page_num for signal in unclaimed})
        page_text = "、".join(str(page) for page in pages[:12])
        extra = " 等" if len(pages) > 12 else ""
        warnings.append(
            f"第 {page_text}{extra} 页有 {len(unclaimed)} 处写明「废标」或「投标无效」的内容，"
            "还没有提取成单独的一条招标要求。请对照原文检查：是漏了，还是几条被合成了一条。"
        )
    if missing_anchors > 0:
        warnings.append(
            f"否决或资格相关章节里，带序号或★的条件比当前招标要求多 {missing_anchors} 条。"
            "请核对这些条款是否都已提取出来。"
        )
    auto_text = _format_auto_handled_warning(promoted, inserted)
    if auto_text:
        warnings.append(auto_text)
    return warnings


def _format_auto_handled_warning(promoted: int, inserted: int) -> str:
    """拼「系统已自动处理」句；只报实际发生的抬标/补提，不写 0 条。"""
    parts: list[str] = []
    if promoted:
        parts.append(f"{promoted} 条已有要求已标为否决项")
    if inserted:
        prefix = "另新增" if promoted else "新增"
        parts.append(f"{prefix} {inserted} 条独立否决条件")
    if not parts:
        return ""
    return "系统已自动处理：" + "，".join(parts) + "。"
