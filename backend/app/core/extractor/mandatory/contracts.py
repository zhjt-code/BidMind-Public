"""
职责: 高风险原子条件召回的纯数据契约。
不做: 不定位置信度、不调用 LLM、不访问 DB、不落库。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PageText:
    page_num: int
    text: str
    section_path: str = ""


@dataclass(frozen=True)
class ScopeWindow:
    pages: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class MandatorySignal:
    page_num: int
    kind: str
    snippet: str


@dataclass(frozen=True)
class AtomicCondition:
    description: str
    category: str
    source_pages: tuple[int, ...]
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExistingRequirementView:
    index: int
    requirement_id: int | None
    description: str
    is_mandatory: bool
    requirement_kind: str
    source_pages: tuple[int, ...] = ()


@dataclass
class CoverageDecision:
    promote_indices: list[int] = field(default_factory=list)
    inserts: list[AtomicCondition] = field(default_factory=list)
    unclaimed: list[MandatorySignal] = field(default_factory=list)
    missing_anchor_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class CoverageReport:
    promoted_count: int = 0
    inserted_count: int = 0
    unclaimed_count: int = 0
    warnings: list[str] = field(default_factory=list)
