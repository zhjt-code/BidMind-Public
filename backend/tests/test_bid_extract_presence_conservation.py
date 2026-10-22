""" 提取侧存在性守恒。

覆盖风险点:
1. 分面缺口但库存材料级命中 → 禁止 not_found;
2. facet_states 按分面落库 (found/missing 并存的 AND 汇总);
3. 种子页并入检索袋;
4. 袋回流合并不丢身份。
夹具: 准入证 / 授权委托书 / 社保缴费证明。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.extractor.evidence_verdict import decide_bid_response_persist
from app.core.extractor.facet_closure import (
    PRESENCE_FOUND,
    build_facet_states,
    coverage_with_inventory,
    reconcile_review_gaps_with_inventory,
    rewrite_missing_elements,
)
from app.core.retriever.evidence_coverage import (
    EvidenceFacet,
    evaluate_facet_coverage,
)
from app.core.retriever.material_inventory import (
    SOURCE_RETRIEVAL_BAG,
    MaterialFact,
    facts_from_chunk,
    merge_fact_union,
)
from app.core.retriever.semantic_retriever import SearchResult


def _fact(
    noun: str,
    *,
    page: int,
    chunk_id: int,
    content: str,
    grade: str | None = None,
    document_id: int = 1,
    bid_part: str | None = "commercial",
) -> MaterialFact:
    fact = facts_from_chunk(
        noun=noun,
        chunk_id=chunk_id,
        document_id=document_id,
        bid_part=bid_part,
        physical_page=page,
        content=content,
        chunk_type="image_evidence",
        source_channel="lexical",
    )
    assert fact is not None
    if grade is not None and fact.grade != grade:
        fact = MaterialFact(
            canonical_material_key=fact.canonical_material_key,
            raw_noun=fact.raw_noun,
            grade=grade,
            document_id=fact.document_id,
            bid_part=fact.bid_part,
            chunk_id=fact.chunk_id,
            physical_page=fact.physical_page,
            identifier=fact.identifier,
            subject=fact.subject,
            source_channel=fact.source_channel,
            excerpt=fact.excerpt,
        )
    return fact


def _sr(chunk_id: int, content: str, page: int = 40) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=1,
        content=content,
        page_num=page,
        similarity=0.4,
        role="primary",
    )


class TestPersistForbidsNotFound:
    def test_admission_cert_inventory_hit_packs_partial(self):
        verdict = decide_bid_response_persist(
            facet_missing=True,
            starvation=False,
            pack_has_substantial=False,
            inventory_material_hit=True,
            inventory_pages=[40],
        )
        assert verdict.action == "pack_as_partial"
        assert "第40页" in (verdict.relevance_note or "")

    def test_authorization_letter_second_entity(self):
        verdict = decide_bid_response_persist(
            facet_missing=True,
            starvation=False,
            pack_has_substantial=False,
            inventory_material_hit=True,
            inventory_pages=[6, 7],
        )
        assert verdict.action != "not_found"
        assert "第6页" in (verdict.relevance_note or "")


class TestFacetStatesAndAnd:
    def test_partial_obligations_keep_found_and_missing(self):
        facets = (
            EvidenceFacet(key="准入证", query="准入证"),
            EvidenceFacet(key="社保缴费证明", query="社保缴费证明"),
        )
        facts = (
            _fact(
                "准入证",
                page=40,
                chunk_id=11,
                content="专业准入证 编号YZ001 有效期至2028年",
            ),
        )
        coverage = coverage_with_inventory(
            facets,
            [_sr(1, "无关目录行", page=1)],
            facts,
        )
        assert "准入证" in coverage.covered_keys
        assert "社保缴费证明" in coverage.missing_keys
        states = build_facet_states(
            facets, coverage, facts, llm_missing=["社保缴费证明"]
        )
        by_key = {item["facet_key"]: item for item in states}
        assert by_key["准入证"]["presence"] == PRESENCE_FOUND
        assert by_key["准入证"]["evidence_refs"]
        assert by_key["社保缴费证明"]["presence"] != PRESENCE_FOUND
        rewritten = rewrite_missing_elements(["准入证", "社保缴费证明"], facts)
        assert any("已核实存在于" in item and "准入证" in item for item in rewritten)
        assert "社保缴费证明" in rewritten


class TestSeedAndBagMerge:
    def test_inventory_upgrades_coverage_without_relaxing_empty_bag(self):
        facets = (EvidenceFacet(key="授权委托书", query="授权委托书"),)
        facts = (
            _fact(
                "授权委托书",
                page=8,
                chunk_id=21,
                content='授权委托书 兹授权<PERSON_B>办理投标事宜，授权期限至招标结束。',
            ),
        )
        empty = evaluate_facet_coverage(facets, [])
        assert "授权委托书" in empty.missing_keys
        upgraded = coverage_with_inventory(facets, [], facts)
        assert "授权委托书" in upgraded.covered_keys

    def test_retrieval_bag_union_keeps_identity(self):
        lexical = _fact(
            "社保缴费证明",
            page=90,
            chunk_id=31,
            content="社会保险缴费证明 编号(豫)社缴字20240001 <ORG_23>",
        )
        bag = MaterialFact(
            canonical_material_key=lexical.canonical_material_key,
            raw_noun=lexical.raw_noun,
            grade="mention",
            document_id=lexical.document_id,
            bid_part=lexical.bid_part,
            chunk_id=lexical.chunk_id,
            physical_page=lexical.physical_page,
            identifier=lexical.identifier,
            subject=lexical.subject,
            source_channel=SOURCE_RETRIEVAL_BAG,
            excerpt="社保缴费证明（目录）",
        )
        merged = merge_fact_union([lexical, bag])
        assert len(merged) == 1
        assert merged[0].grade == "material"
        assert merged[0].source_channel != SOURCE_RETRIEVAL_BAG


class TestReviewGapReconcile:
    def test_ledger_presence_flag_and_display_rewrite(self):
        facets = (EvidenceFacet(key="准入证", query="准入证"),)
        facts = (
            _fact(
                "准入证",
                page=40,
                chunk_id=11,
                content="专业准入证 编号YZ001 有效期至2028年",
            ),
        )
        display, remaining, flags = reconcile_review_gaps_with_inventory(
            ["准入证"], ["准入证"], facets, facts
        )
        assert remaining == ()
        assert any(item.startswith("ledger_presence:") for item in flags)
        assert any("待核适用性" in item and "第40页" in item for item in display)
