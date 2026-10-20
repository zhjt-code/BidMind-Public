""" 材料探针索引与尾字早停不得改变命中集合。

问题形态: 全量行乘全量键的同步匹配。本案例与同形态第二例使用不同材料名。
"""

from __future__ import annotations
from tests.synthetic_data import synthetic_text

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)

from app.core.ai.reviewer_parts.evidence_ledger.builder import collect_atoms_from_rows
from app.core.ai.reviewer_parts.evidence_ledger.contracts import EvidenceLedger
from app.core.retriever.evidence_coverage import EvidenceFacet, build_evidence_facets
from app.core.retriever.material_inventory import (
    facts_from_chunk,
    scan_hits_into_facts,
)
from app.core.retriever.material_key import (
    MATCH_NONE,
    MATCH_STRONG,
    MATCH_WEAK,
    _CONTAINS_STRONG_MIN,
    canonical_material_key,
    match_key_in_compact,
    match_material_in_text,
    match_material_keys,
    normalize_material_compact,
)


def _match_without_tail_reject(noun: str, text: str) -> str:
    """尾字早停引入前的逐窗扫描，用作等价神谕。"""
    key = canonical_material_key(noun)
    compact = normalize_material_compact(text)
    if not key or not compact:
        return MATCH_NONE
    if key in compact:
        return MATCH_STRONG if len(key) >= _CONTAINS_STRONG_MIN else MATCH_WEAK
    best = MATCH_NONE
    for size in range(min(len(key), 8), 1, -1):
        needle = key[-size:]
        start = 0
        while True:
            pos = compact.find(needle, start)
            if pos < 0:
                break
            window_start = max(0, pos + size - max(len(key), 12))
            window = compact[window_start : pos + size]
            level = match_material_keys(key, window)
            if level == MATCH_STRONG:
                return MATCH_STRONG
            if level == MATCH_WEAK:
                best = MATCH_WEAK
            start = pos + 1
    return best


def _identities(atoms) -> list[tuple]:
    return [(atom.kind, atom.page, atom.chunk_id) for atom in atoms]


def test_tail_reject_matches_full_window_for_two_materials() -> None:
    pairs = (
        ("安全生产许可证", "见许可证附件"),
        ("安全生产许可证", "今天天气晴朗"),
        ("中标通知书", "见通知书附件"),
        ("中标通知书", "今天天气晴朗"),
        ("社保缴费证明", "附社保缴费证明扫描件"),
    )
    slow_hits = 0
    for noun, text in pairs:
        assert match_material_in_text(noun, text) == _match_without_tail_reject(noun, text)
        key = canonical_material_key(noun)
        compact = normalize_material_compact(text)
        assert match_key_in_compact(key, compact) == match_material_in_text(noun, text)
        if key not in compact and key[-2:] in compact:
            slow_hits += 1
    assert slow_hits >= 2


def test_probe_index_matches_scan_for_safety_license() -> None:
    rows = [{
        "bid_response_id": 10,
        "chunk_id": 9001,
        "page": 612,
        "document_id": 1,
        "matched_sub_query": "资格审查材料",
        "content": "安全生产许可证 编号(豫)JZ安许证字0001 有效期至2028年",
        "chunk_type": "image_evidence",
    }]
    facets_a = build_evidence_facets("投标人须提供资格审查材料原件", [])
    facets_b = build_evidence_facets("投标人须具备营业执照及安全生产许可证", [])
    indexed = collect_atoms_from_rows(rows, {10: facets_a, 20: facets_b}, {10: 1, 20: 2})
    plain = EvidenceLedger(atoms=indexed.atoms)
    assert indexed.probe_index is not None
    assert _identities(indexed.atoms_for_facets(facets_b)) == _identities(
        plain.atoms_for_facets(facets_b)
    )
    assert any(atom.page == 612 for atom in indexed.atoms_for_facets(facets_b))


def test_probe_index_matches_scan_for_business_license() -> None:
    """同形态第二例: 营业执照。"""
    rows = [{
        "bid_response_id": 30,
        "chunk_id": 9002,
        "page": 608,
        "document_id": 1,
        "matched_sub_query": "证照原件",
        "content": synthetic_text('营业执照 ', '统一社会信用代码 ', '91320100M', 'A1ABCDE1X'),
        "chunk_type": "image_evidence",
    }]
    facets_a = build_evidence_facets("投标人须提交证照原件", [])
    facets_b = build_evidence_facets("投标人须具备有效营业执照及施工资质", [])
    indexed = collect_atoms_from_rows(rows, {30: facets_a, 40: facets_b}, {30: 3, 40: 4})
    plain = EvidenceLedger(atoms=indexed.atoms)
    assert _identities(indexed.atoms_for_facets(facets_b)) == _identities(
        plain.atoms_for_facets(facets_b)
    )
    assert any(atom.page == 608 for atom in indexed.material_atoms_for_facets(facets_b))
    unknown = (EvidenceFacet(key="冰川冰芯年代", query="冰川冰芯年代"),)
    assert indexed.atoms_for_facets(unknown) == []
    assert indexed.atoms_for_facets(unknown) == plain.atoms_for_facets(unknown)


def test_scan_hits_matches_direct_facts_for_two_nouns() -> None:
    prepared = (
        (1, 10, "安全生产许可证 编号JZ0001", "image_evidence", 12, "commercial"),
        (2, 10, synthetic_text('营业执照 ', '统一社会信用代码 ', '91320100M', 'A1ABCDE1X'), "image_evidence", 8, "commercial"),
    )
    nouns = ("安全生产许可证", "营业执照")
    scanned = scan_hits_into_facts(prepared, nouns, "retrieval_bag")
    direct = []
    for chunk_id, document_id, content, chunk_type, page, bid_part in prepared:
        for noun in nouns:
            fact = facts_from_chunk(
                noun=noun,
                chunk_id=chunk_id,
                document_id=document_id,
                bid_part=bid_part,
                physical_page=page,
                content=content,
                chunk_type=chunk_type,
                source_channel="retrieval_bag",
            )
            if fact is not None:
                direct.append(fact)
    assert [item.canonical_material_key for item in scanned] == [
        item.canonical_material_key for item in direct
    ]
    assert [item.chunk_id for item in scanned] == [item.chunk_id for item in direct]
    assert scan_hits_into_facts(prepared, ("冰川冰芯年代",), "retrieval_bag") == []
