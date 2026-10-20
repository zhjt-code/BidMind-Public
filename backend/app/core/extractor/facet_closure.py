"""
职责: 分面级取证闭合与 facet_states 构造
不做: LLM、落库
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retriever.evidence_coverage import (
    EvidenceFacet,
    FacetCoverage,
    evaluate_facet_coverage,
    merge_search_results,
)
from app.core.retriever.material_inventory import (
    MaterialFact,
    inventory_strong_hits,
)
from app.core.retriever.semantic_retriever import SearchResult
from app.models.chunk import Chunk

FACET_SEED_LIMIT = 3
PRESENCE_FOUND = "found"
PRESENCE_MISSING = "missing"
PRESENCE_INSUFFICIENT = "insufficient"


def coverage_with_inventory(
    facets: Sequence[EvidenceFacet],
    results: Sequence[SearchResult],
    facts: Sequence[MaterialFact],
) -> FacetCoverage:
    """分面 covered = 既有 facet_hit ∨ 库存材料级强匹配。"""
    base = evaluate_facet_coverage(facets, results)
    if not facts:
        return base
    covered = list(base.covered_keys)
    missing: list[str] = []
    covered_set = set(covered)
    for key in base.missing_keys:
        if inventory_strong_hits(facts, key, material_grade_only=True):
            if key not in covered_set:
                covered.append(key)
                covered_set.add(key)
        else:
            missing.append(key)
    return FacetCoverage(
        expected_keys=base.expected_keys,
        covered_keys=tuple(covered),
        missing_keys=tuple(missing),
    )


def build_facet_states(
    facets: Sequence[EvidenceFacet],
    coverage: FacetCoverage,
    facts: Sequence[MaterialFact],
    llm_missing: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """每分面 {facet_key, canonical_material_key, presence, evidence_refs, missing_reason}。"""
    covered = set(coverage.covered_keys)
    missing_text = " ".join(str(item) for item in llm_missing if item)
    states: list[dict[str, Any]] = []
    for facet in facets:
        hits = inventory_strong_hits(facts, facet.key, material_grade_only=True)
        refs = [
            {
                "document_id": hit.document_id,
                "chunk_id": hit.chunk_id,
                "page": hit.physical_page,
            }
            for hit in hits[:FACET_SEED_LIMIT]
        ]
        key = facet.key
        if hits or key in covered:
            presence = PRESENCE_FOUND
            reason = None
        elif missing_text and key in missing_text:
            presence = PRESENCE_INSUFFICIENT
            reason = "llm_missing_and_inventory_empty"
        else:
            presence = PRESENCE_MISSING
            reason = "no_seed_no_inventory"
        states.append(
            {
                "facet_key": key,
                "canonical_material_key": key,
                "presence": presence,
                "evidence_refs": refs,
                "missing_reason": reason,
            }
        )
    return states


FOUND_TEMPLATE_EXTRACT = "{text}已核实存在于{page_note}"
FOUND_TEMPLATE_REVIEW = "{text}存在于{page_note}待核适用性"


def rewrite_missing_elements(
    elements: Sequence[str],
    facts: Sequence[MaterialFact],
    *,
    found_template: str = FOUND_TEMPLATE_EXTRACT,
) -> list[str]:
    """库存材料级命中的缺失要素改写为带页码的已核实表述。"""
    rewritten: list[str] = []
    for item in elements or ():
        text = str(item or "").strip()
        if not text:
            continue
        hits = inventory_strong_hits(facts, text, material_grade_only=True)
        if not hits:
            rewritten.append(text)
            continue
        pages = sorted({hit.physical_page for hit in hits if hit.physical_page})
        page_note = "、".join(f"第{page}页" for page in pages[:3]) or "已核实页"
        rewritten.append(found_template.format(text=text, page_note=page_note))
    return rewritten


def reconcile_review_gaps_with_inventory(
    missing_elements: Sequence[str],
    facet_missing: Sequence[str],
    facets: Sequence[EvidenceFacet],
    facts: Sequence[MaterialFact],
) -> tuple[list[str], tuple[str, ...], tuple[str, ...]]:
    """审查装配前与库存对账: 展示改写、仍缺分面、ledger_presence 旗标。"""
    display = rewrite_missing_elements(
        missing_elements, facts, found_template=FOUND_TEMPLATE_REVIEW
    )
    remaining: list[str] = []
    flags: list[str] = []
    seen: set[str] = set()

    def add_flag(key: str) -> None:
        token = f"ledger_presence:{key}"[:120]
        if token in seen:
            return
        seen.add(token)
        flags.append(token)

    for key in facet_missing or ():
        if inventory_strong_hits(facts, str(key), material_grade_only=True):
            add_flag(str(key))
        else:
            remaining.append(str(key))
    for facet in facets or ():
        key = str(getattr(facet, "key", "") or "")
        if key and inventory_strong_hits(facts, key, material_grade_only=True):
            add_flag(key)
    return display, tuple(remaining), tuple(flags)


def format_verified_materials_block(
    facts: Sequence[MaterialFact],
    facets: Sequence[EvidenceFacet],
) -> str:
    """提取 Prompt 硬证据块: 已核实材料清单（页码）。"""
    lines: list[str] = []
    seen: set[tuple[str, int, int]] = set()
    nouns = [facet.key for facet in facets] or [
        fact.canonical_material_key for fact in facts
    ]
    for noun in nouns:
        for hit in inventory_strong_hits(facts, noun, material_grade_only=False):
            identity = (hit.canonical_material_key, hit.document_id, hit.physical_page)
            if identity in seen:
                continue
            seen.add(identity)
            grade = "材料级" if hit.grade == "material" else "提及级"
            page = hit.physical_page or 0
            lines.append(f"- {hit.canonical_material_key} / {grade} / 第{page}页")
            if len(lines) >= 12:
                break
        if len(lines) >= 12:
            break
    if not lines:
        return ""
    return (
        "## 已核实材料清单（硬事实）\n"
        "下列材料已在本项目投标文件中核实存在，禁止列入缺失，不得据此标 found=false。\n"
        + "\n".join(lines)
    )


def inventory_hit_pages(facts: Sequence[MaterialFact], facets: Sequence[EvidenceFacet]) -> list[int]:
    pages: list[int] = []
    for facet in facets:
        for hit in inventory_strong_hits(facts, facet.key, material_grade_only=True):
            if hit.physical_page:
                pages.append(hit.physical_page)
    return list(dict.fromkeys(pages))


async def merge_inventory_seeds(
    session: AsyncSession,
    results: Sequence[SearchResult],
    facets: Sequence[EvidenceFacet],
    facts: Sequence[MaterialFact],
    coverage: Optional[FacetCoverage] = None,
) -> list[SearchResult]:
    """缺口分面按库存强匹配取种子页 chunk, 不受页序帽与 top_k 尾截。"""
    missing = list((coverage or evaluate_facet_coverage(facets, results)).missing_keys)
    if not missing or not facts:
        return list(results)
    seeds: list[SearchResult] = []
    chunk_ids: list[int] = []
    for key in missing:
        hits = inventory_strong_hits(facts, key, material_grade_only=True)
        for hit in hits[:FACET_SEED_LIMIT]:
            if hit.chunk_id not in chunk_ids:
                chunk_ids.append(hit.chunk_id)
    if not chunk_ids:
        return list(results)
    rows = list(
        (
            await session.execute(select(Chunk).where(Chunk.id.in_(chunk_ids)))
        ).scalars().all()
    )
    by_id = {int(chunk.id): chunk for chunk in rows}
    for chunk_id in chunk_ids:
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        seeds.append(
            SearchResult.from_chunk(chunk, similarity=1.0, role="inventory")
        )
    return merge_search_results(results, seeds)
