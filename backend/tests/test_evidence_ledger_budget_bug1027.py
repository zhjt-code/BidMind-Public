""" 项目证据账本注入审查 Prompt 的条数/字符双帽。

职责: 覆盖万级原子注入仍有界、材料级优先、gate 原子集不裁
不做: 真实 DB、真实 LLM、笛卡尔登记重构
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.ai.reviewer_parts.evidence_ledger.contracts import (
    EvidenceAtom,
    EvidenceLedger,
)
from app.core.ai.reviewer_parts.evidence_prefetch import attach_ledger_evidence
from app.core.config import settings
from app.core.retriever.evidence_coverage import EvidenceFacet
from app.core.review_contracts import ReviewEvidenceBundle

CHAR_BUDGET = int(settings.REVIEW_LEDGER_CHAR_BUDGET)
PER_KIND = int(settings.REVIEW_LEDGER_ATOMS_PER_KIND)


def _atom(
    kind: str,
    *,
    grade: str = "mention",
    page: int | None = 1,
    chunk_id: int = 1,
    source_req: int = 1,
    excerpt: str = "",
) -> EvidenceAtom:
    return EvidenceAtom(
        kind=kind,
        grade=grade,
        page=page,
        chunk_id=chunk_id,
        excerpt=excerpt or f"{kind} 摘录{chunk_id}",
        source_requirement_ids=(source_req,),
    )


def _bulk(
    kind: str,
    n: int,
    *,
    grade: str = "mention",
    source_req: int = 1,
    chunk_start: int = 1000,
) -> list[EvidenceAtom]:
    return [
        _atom(
            kind,
            grade=grade,
            page=(i % 180) + 1,
            chunk_id=chunk_start + i,
            source_req=source_req,
        )
        for i in range(n)
    ]


def _bundle(**overrides) -> ReviewEvidenceBundle:
    payload = dict(
        response_content="",
        source_pages=[],
        completeness="partial",
        missing_elements=[],
        linked_to=None,
        evidence_type="certificate",
        evidence_anchor=None,
        relevance_score=None,
        relevance_note=None,
        image_evidence="",
        cross_category_evidence="",
        quality_flags=[],
    )
    payload.update(overrides)
    return ReviewEvidenceBundle(**payload)


def _render(
    atoms: list[EvidenceAtom],
    facets: tuple[EvidenceFacet, ...],
    *,
    char_budget: int = CHAR_BUDGET,
    per_kind_limit: int = PER_KIND,
) -> str:
    return EvidenceLedger.to_budgeted_prompt_block(
        atoms,
        facets,
        char_budget=char_budget,
        per_kind_limit=per_kind_limit,
    )


def test_empty_atoms_render_empty_block() -> None:
    facets = (EvidenceFacet(key="设备仪器", query="设备仪器"),)
    assert _render([], facets) == ""


def test_no_facets_still_bounds_output() -> None:
    atoms = _bulk("设备仪器", 400) + [
        _atom("检测设备", grade="material", page=9, chunk_id=9),
    ]
    block = _render(atoms, ())
    assert block
    assert len(block) <= CHAR_BUDGET
    assert "检测设备" in block


def test_equipment_generic_facet_caps_thousands_of_atoms() -> None:
    """夹具 1: 设备类泛分面键 × 数千原子，材料级 kind 与精确键页码仍在。"""
    facets = (EvidenceFacet(key="设备仪器", query="投标人须提供设备仪器"),)
    atoms: list[EvidenceAtom] = [
        _atom("设备仪器", grade="material", page=12, chunk_id=12, excerpt="设备仪器清单扫描件"),
        _atom("检测设备", grade="material", page=20, chunk_id=20, excerpt="检测设备校准报告"),
        _atom("校准证书", grade="material", page=33, chunk_id=33, excerpt="计量校准证书原件"),
    ]
    atoms.extend(_bulk("设备仪器", 3000, chunk_start=10000))
    atoms.extend(
        _atom(f"设备型号{i}", page=200 + i, chunk_id=50000 + i)
        for i in range(200)
    )
    uncapped = EvidenceLedger.to_prompt_block(atoms)
    block = _render(atoms, facets)
    assert len(uncapped) > CHAR_BUDGET
    assert len(block) <= CHAR_BUDGET
    assert "设备仪器" in block
    assert "检测设备" in block
    assert "校准证书" in block
    assert "第12页" in block
    assert "另见" in block
    assert "因篇幅省略" in block


def test_personnel_certificate_second_entity_same_cap() -> None:
    """夹具 2: 人员证书类分面键，同形态不同实体，同一帽与优先级。"""
    facets = (EvidenceFacet(key="注册安全工程师证书", query="注册安全工程师证书"),)
    atoms: list[EvidenceAtom] = [
        _atom(
            "注册安全工程师证书",
            grade="material",
            page=8,
            chunk_id=8,
            excerpt="注册安全工程师证书扫描件",
        ),
        _atom("身份证", grade="material", page=3, chunk_id=3, excerpt="身份证复印件"),
        _atom("社保证明", grade="material", page=41, chunk_id=41, excerpt="社保证明原件"),
    ]
    atoms.extend(_bulk("注册安全工程师证书", 2500, chunk_start=20000))
    atoms.extend(
        _atom(f"人员资格{i}", page=300 + i, chunk_id=60000 + i)
        for i in range(200)
    )
    uncapped = EvidenceLedger.to_prompt_block(atoms)
    block = _render(atoms, facets)
    assert len(uncapped) > CHAR_BUDGET
    assert len(block) <= CHAR_BUDGET
    assert "注册安全工程师证书" in block
    assert "身份证" in block
    assert "社保证明" in block
    assert "第8页" in block
    assert "因篇幅省略" in block


def test_overflow_line_counts_omitted_mention_kinds() -> None:
    facets = (EvidenceFacet(key="方案", query="方案"),)
    atoms = [_atom("实施方案", grade="material", page=1, chunk_id=1)]
    atoms.extend(_atom(f"方案附件{i}", page=10 + i, chunk_id=100 + i) for i in range(60))
    block = _render(atoms, facets, char_budget=900, per_kind_limit=1)
    assert "实施方案" in block
    assert "因篇幅省略" in block
    assert "提及级原子" in block


def test_many_exact_material_kinds_all_listed_before_mentions() -> None:
    """精确材料级 kind 先各占一行，避免每种三行挤掉同条其它精确键。"""
    exact_kinds = [f"保证金递交要求{i:02d}项" for i in range(12)]
    facets = tuple(EvidenceFacet(key=kind, query=kind) for kind in exact_kinds)
    atoms = [
        _atom(kind, grade="material", page=10 + i, chunk_id=10 + i)
        for i, kind in enumerate(exact_kinds)
    ]
    atoms.extend(_atom(f"其它材料{i}", page=80 + i, chunk_id=80 + i) for i in range(80))
    block = _render(atoms, facets)
    assert len(block) <= CHAR_BUDGET
    for kind in exact_kinds:
        assert kind in block


def test_attach_keeps_full_atom_set_for_gate() -> None:
    injected_n = 40
    atoms = tuple(_bulk("安全生产许可证", injected_n, grade="material"))
    ledger = EvidenceLedger(atoms=atoms)
    orchestrator = SimpleNamespace(_evidence_ledger=ledger)
    facets = (EvidenceFacet(key="安全生产许可证", query="安全生产许可证"),)
    pair = MagicMock()
    pair.requirement_id = 2
    pair._gate_ledger_atoms = []
    bundle = _bundle(retrieval_chunk_pages=set())
    attach_ledger_evidence(orchestrator, pair, facets, bundle)
    assert len(bundle.ledger_atoms) == injected_n
    assert len(pair._gate_ledger_atoms) == injected_n
    assert len(bundle.ledger_evidence) <= CHAR_BUDGET
    assert "已核实材料" in bundle.ledger_evidence
    assert "安全生产许可证" in bundle.ledger_evidence
