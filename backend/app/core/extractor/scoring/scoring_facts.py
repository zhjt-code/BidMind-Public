"""评分事实集合。

职责: 承载带稳定证据引用的不可变评分事实集合。
不做: 不解析PDF/表格、不访问数据库、不调用LLM、不生成页面投影。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class ScoringScopeKey(BaseModel):
    """评分事实所属的方案、采购包与卷作用域(scheme > lot > volume,)。"""

    SINGLE_SCHEME: ClassVar[str] = "__single__"
    SINGLE_LOT: ClassVar[str] = "__single__"
    UNASSIGNED_LOT: ClassVar[str] = "__unassigned__"
    UNSPECIFIED_VOLUME: ClassVar[str] = "unspecified"

    scheme: str = SINGLE_SCHEME
    lot: str = SINGLE_LOT
    volume: str = UNSPECIFIED_VOLUME

    @classmethod
    def from_item(cls, item: Any) -> "ScoringScopeKey":
        """从评分项归一化作用域。"""
        raw_scheme = str(getattr(item, "scheme", "") or "").strip()
        raw_lot = str(getattr(item, "lot", "") or "").strip()
        raw_volume = str(getattr(item, "volume", "") or "").strip()
        return cls(
            scheme=raw_scheme or cls.SINGLE_SCHEME,
            lot=raw_lot or cls.SINGLE_LOT,
            volume=raw_volume or cls.UNSPECIFIED_VOLUME,
        )


class ScoringEvidenceRef(BaseModel):
    """评分事实的稳定证据引用。"""

    evidence_key: str
    source_id: str
    parsed_item_index: int
    page_range: str
    quote_digest: str
    evidence_quote: str = ""


class ScoringFact(BaseModel):
    """单条评分事实。"""

    fact_key: str
    scope: ScoringScopeKey
    score_item_name: str
    description: str
    score_weight: float
    # 事实类型(Stage-3 透传，用于旧门禁: base_score/price_formula 零分视为缺失)
    fact_type: str = "base_score"
    volume_weight: float | None = None
    volume_max: float | None = None
    score_rules: list[str] = Field(default_factory=list)
    deduction_rules: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    authority: str = "deterministic"
    evidence_refs: list[ScoringEvidenceRef] = Field(default_factory=list)


class ScoringFactSetInput(BaseModel):
    """构建评分事实集合的输入。"""

    items: list[Any] = Field(default_factory=list)
    source_id: str = "unknown"
    authority: str = "deterministic"

    class Config:
        arbitrary_types_allowed = True


class ScoringFactSet(BaseModel):
    """评分事实集合，保留观察事实和有效事实投影。"""

    source_id: str
    observed_facts: list[ScoringFact] = Field(default_factory=list)
    effective_fact_keys: list[str] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def effective_facts(self) -> list[ScoringFact]:
        """按 effective_fact_keys 返回有效事实。"""
        by_key: dict[str, ScoringFact] = {}
        for fact in self.observed_facts:
            by_key.setdefault(fact.fact_key, fact)
        return [by_key[key] for key in self.effective_fact_keys if key in by_key]

    @property
    def total_score(self) -> float:
        """有效评分事实满分合计。"""
        return round(sum(float(fact.score_weight or 0) for fact in self.effective_facts), 4)

    def totals_by_lot(self) -> dict[str, float]:
        """按采购包合计有效评分事实满分。"""
        totals: dict[str, float] = {}
        for fact in self.effective_facts:
            lot = fact.scope.lot
            totals[lot] = round(totals.get(lot, 0.0) + float(fact.score_weight or 0), 4)
        return totals

    def totals_by_scheme_lot(self) -> dict[tuple[str, str], float]:
        """按(评分方案, 采购包)合计有效评分事实满分。"""
        totals: dict[tuple[str, str], float] = {}
        for fact in self.effective_facts:
            key = (fact.scope.scheme, fact.scope.lot)
            totals[key] = round(totals.get(key, 0.0) + float(fact.score_weight or 0), 4)
        return totals


def build_scoring_fact_set(
    items: list[Any],
    *,
    source_id: str = "unknown",
    authority: str = "deterministic",
) -> ScoringFactSet:
    """从评分项构建保留证据的评分事实集合。"""
    input_data = ScoringFactSetInput(items=items, source_id=source_id, authority=authority)
    observed: list[ScoringFact] = []
    effective_by_key: dict[str, ScoringFact] = {}
    for idx, item in enumerate(input_data.items):
        fact = _item_to_fact(item, input_data.source_id, idx, input_data.authority)
        observed.append(fact)
        if fact.fact_key in effective_by_key:
            existing = effective_by_key[fact.fact_key]
            merged_refs = [*existing.evidence_refs, *fact.evidence_refs]
            effective_by_key[fact.fact_key] = existing.model_copy(
                update={"evidence_refs": merged_refs}
            )
        else:
            effective_by_key[fact.fact_key] = fact

    merged_observed = [
        effective_by_key.get(fact.fact_key, fact)
        if fact.fact_key in effective_by_key and fact.fact_key not in {
            prev.fact_key for prev in observed[:pos]
        }
        else fact
        for pos, fact in enumerate(observed)
    ]
    # Keep every observed row, but ensure the first occurrence carries merged refs for effective projection.
    first_seen: set[str] = set()
    normalized_observed: list[ScoringFact] = []
    for fact in merged_observed:
        if fact.fact_key in first_seen:
            normalized_observed.append(fact)
            continue
        first_seen.add(fact.fact_key)
        normalized_observed.append(effective_by_key[fact.fact_key])

    return ScoringFactSet(
        source_id=input_data.source_id,
        observed_facts=normalized_observed,
        effective_fact_keys=list(effective_by_key.keys()),
    )


def _item_to_fact(item: Any, source_id: str, index: int, authority: str) -> ScoringFact:
    scope = ScoringScopeKey.from_item(item)
    score = _coerce_score(getattr(item, "score_weight", 0))
    name = str(getattr(item, "score_item_name", "") or "").strip()
    description = str(getattr(item, "description", "") or "").strip()
    pages = _pages(getattr(item, "source_pages", None))
    fact_type = str(getattr(item, "fact_type", "") or "base_score")
    quote = description or name
    evidence_ref = ScoringEvidenceRef(
        evidence_key=_evidence_key(source_id, index, pages, quote),
        source_id=source_id,
        parsed_item_index=index,
        page_range=_page_key(pages),
        quote_digest=_digest(quote),
        evidence_quote=quote[:500],
    )
    fact_key = _fact_key(scope, name, score, pages)
    return ScoringFact(
        fact_key=fact_key,
        scope=scope,
        score_item_name=name,
        description=description,
        score_weight=score,
        fact_type=fact_type,
        volume_weight=_optional_float(getattr(item, "volume_weight", None)),
        volume_max=_optional_float(getattr(item, "volume_max", None)),
        score_rules=list(getattr(item, "score_rules", []) or []),
        deduction_rules=list(getattr(item, "deduction_rules", []) or []),
        source_pages=pages,
        authority=authority,
        evidence_refs=[evidence_ref],
    )


def _fact_key(scope: ScoringScopeKey, name: str, score: float, pages: list[int]) -> str:
    """事实身份键: 作用域(scheme/lot/volume)+归一名+满分+页位置。

 缺口C(2026-07-17 样本审计): 键中无页码时, 同名同分同卷不同页的两个真实评分事实
 (样本8"相邻两个业绩项均2分"形态)会被 effective 投影静默合并吞分, 并误触发
 EVIDENCE_GAP 修补; 页位置分量与 scoring_identity.build_scoring_identity 口径对齐。
 scheme 分量: 不同评分方案的同名同分项是不同事实, 禁止跨方案合并。
 """
    normalized_name = re.sub(r"[\s:：;；,，。]", "", str(name or "")).lower()
    raw = (
        f"{scope.scheme}|{scope.lot}|{scope.volume}|{_page_key(pages)}"
        f"|{normalized_name}|{score:.4f}"
    )
    return _digest(raw)


def _evidence_key(source_id: str, index: int, pages: list[int], quote: str) -> str:
    raw = f"{source_id}|{index}|{_page_key(pages)}|{_digest(quote)}"
    return _digest(raw)


def _coerce_score(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return round(value, 4)


def _optional_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return round(value, 4) if value >= 0 else None


def _pages(raw_pages: Any) -> list[int]:
    if isinstance(raw_pages, list):
        values = raw_pages
    else:
        values = []
    pages: list[int] = []
    for page in values:
        try:
            page_int = int(page)
        except (TypeError, ValueError):
            continue
        if page_int > 0:
            pages.append(page_int)
    return sorted(set(pages))


def _page_key(pages: list[int]) -> str:
    if not pages:
        return "unknown"
    if pages == list(range(pages[0], pages[-1] + 1)):
        return f"{pages[0]}-{pages[-1]}"
    return ",".join(str(page) for page in pages)


def _digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:24]


__all__ = [
    "ScoringEvidenceRef",
    "ScoringFact",
    "ScoringFactSet",
    "ScoringFactSetInput",
    "ScoringScopeKey",
    "build_scoring_fact_set",
]
