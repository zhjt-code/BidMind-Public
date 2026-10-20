""" 项目级证据账本：跨条款存在性对打。

职责: 覆盖账本沉淀、材料/提及分级、注入排除、闸门只转复核不改判
不做: 真实 DB、真实 LLM、改 CrossGroupConflictGate
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.ai.gates import build_pipeline_for
from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.ledger_presence import LedgerPresenceGate
from app.core.ai.reviewer_parts.evidence_ledger.builder import collect_atoms_from_rows
from app.core.ai.reviewer_parts.evidence_ledger.contracts import (
    EvidenceAtom,
    EvidenceLedger,
)
from app.core.ai.reviewer_parts.evidence_ledger.grading import grade_atom
from app.core.ai.reviewer_parts.evidence_prefetch import attach_ledger_evidence
from app.core.ai.reviewer_parts.pair_review import build_review_prompt
from app.core.retriever.evidence_coverage import EvidenceFacet, build_evidence_facets
from app.core.review_contracts import ReviewEvidenceBundle
from tests.synthetic_data import synthetic_text


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


def _pair(req_id: int, description: str, own_pages: set[int] | None = None):
    pair = MagicMock()
    pair.requirement_id = req_id
    pair.category = "qualification"
    pair.requirement = SimpleNamespace(id=req_id, description=description, sub_requirements=None)
    pair.is_mandatory = True
    pair.response_id = req_id * 10
    pair.has_response = True
    pair._gate_ledger_atoms = []
    return pair


def _safety_rows() -> list[dict]:
    return [{
        "bid_response_id": 10,
        "chunk_id": 9001,
        "page": 612,
        "document_id": 1,
        "matched_sub_query": "资格审查材料",
        "content": "安全生产许可证 编号(豫)JZ安许证字0001 有效期至2028年",
        "chunk_type": "image_evidence",
    }]


def _license_rows() -> list[dict]:
    return [{
        "bid_response_id": 30,
        "chunk_id": 9002,
        "page": 608,
        "document_id": 1,
        "matched_sub_query": "证照原件",
        "content": synthetic_text('营业执照 ', '统一社会信用代码 ', '91320100M', 'A1ABCDE1X'),
        "chunk_type": "image_evidence",
    }]


def test_safety_license_material_atom_reaches_other_requirement() -> None:
    """本例: A 命中 P612 安全证，B 窗口无该页，账本仍返回材料级原子。"""
    facets_a = build_evidence_facets("投标人须提供资格审查材料原件", [])
    facets_b = build_evidence_facets("投标人须具备营业执照及安全生产许可证", [])
    ledger = collect_atoms_from_rows(
        _safety_rows(),
        {10: facets_a, 20: facets_b},
        {10: 1, 20: 2},
    )
    atoms = ledger.material_atoms_for_facets(facets_b)
    assert any(atom.page == 612 for atom in atoms)
    assert any("安全生产" in atom.kind or "安全生产" in atom.excerpt for atom in atoms)
    assert all(atom.grade == "material" for atom in atoms)


def test_business_license_second_entity_same_mechanism() -> None:
    """同形态第二例: 营业执照 + 18 位统一代码。"""
    facets_a = build_evidence_facets("投标人须提交证照原件", [])
    facets_b = build_evidence_facets("投标人须具备有效营业执照及施工资质", [])
    ledger = collect_atoms_from_rows(
        _license_rows(),
        {30: facets_a, 40: facets_b},
        {30: 3, 40: 4},
    )
    atoms = ledger.material_atoms_for_facets(facets_b)
    assert any(atom.page == 608 for atom in atoms)
    assert any(atom.grade == "material" for atom in atoms)


def test_commitment_mention_does_not_block() -> None:
    """仅承诺书提及不得升材料级，闸门不适用。"""
    assert grade_atom(
        "primary",
        "我方承诺具备安全生产许可证，保证真实有效。",
        "安全生产许可证",
    ) == "mention"
    gate = LedgerPresenceGate()
    ctx = GateContext(
        flow="investigate", stage="post_llm",
        target=SimpleNamespace(comment="未发现投标人安全生产许可证"),
        evidence={"ledger_atoms": [{
            "kind": "安全生产许可证",
            "grade": "mention",
            "page": 8,
            "source_requirement_ids": [1],
        }]},
    )
    assert gate.applies(ctx) is False


def test_rebuttal_marker_passes_gate() -> None:
    gate = LedgerPresenceGate()
    ctx = GateContext(
        flow="investigate", stage="post_llm",
        target=SimpleNamespace(comment="未发现投标人自身的安全生产许可证，该证非投标人自身"),
        evidence={"ledger_atoms": [{
            "kind": "安全生产许可证",
            "grade": "material",
            "page": 612,
            "source_requirement_ids": [1],
        }]},
    )
    assert gate.applies(ctx) is True
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "pass"


def test_gate_marks_conflict_without_lifting_compliance() -> None:
    gate = LedgerPresenceGate()
    ctx = GateContext(
        flow="investigate", stage="post_llm",
        target=SimpleNamespace(
            comment="但未发现投标人自身的安全生产许可证扫描件",
            compliance_level="not",
            score=0,
        ),
        evidence={"ledger_atoms": [{
            "kind": "安全生产许可证",
            "grade": "material",
            "page": 612,
            "source_requirement_ids": [1],
        }]},
    )
    assert gate.applies(ctx) is True
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "warn"
    assert verdict.suggested_patch is None
    assert "compliance_level" not in (verdict.trace or {})


def test_gate_license_second_entity_also_keeps_comment_clean() -> None:
    """同形态第二例: 营业执照否认意见不得把要求主键写入用户可见 comment。"""
    original = "经检索未发现有效营业执照复印件"
    gate = LedgerPresenceGate()
    ctx = GateContext(
        flow="investigate", stage="post_llm",
        target=SimpleNamespace(
            comment=original,
            compliance_level="not",
            score=0,
        ),
        evidence={"ledger_atoms": [{
            "kind": "营业执照",
            "grade": "material",
            "page": 608,
            "source_requirement_ids": [88, 99],
        }]},
    )
    assert gate.applies(ctx) is True
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "warn"
    assert verdict.suggested_patch is None
    assert "comment" not in (verdict.trace or {})
    kinds = (verdict.trace or {}).get("conflict_kinds") or []
    assert any("营业执照" in str(kind) for kind in kinds) or verdict.reason


def test_attach_excludes_own_pages_and_self_source() -> None:
    atom = EvidenceAtom(
        kind="安全生产许可证",
        grade="material",
        page=612,
        chunk_id=9001,
        excerpt="安全生产许可证 编号(豫)JZ",
        source_requirement_ids=(1,),
    )
    ledger = EvidenceLedger(atoms=(atom,))
    orchestrator = SimpleNamespace(_evidence_ledger=ledger)
    facets = (EvidenceFacet(key="安全生产许可证", query="安全生产许可证"),)

    other = _pair(2, "投标人须具备安全生产许可证")
    other_bundle = _bundle(retrieval_chunk_pages=set())
    attach_ledger_evidence(orchestrator, other, facets, other_bundle)
    assert other_bundle.ledger_atoms
    assert "已核实材料" in other_bundle.ledger_evidence

    own = _pair(1, "投标人须提供资格审查材料原件")
    own_bundle = _bundle(retrieval_chunk_pages={612})
    attach_ledger_evidence(orchestrator, own, facets, own_bundle)
    assert own_bundle.ledger_atoms == []


def test_build_review_prompt_contains_ledger_block() -> None:
    pair = _pair(2, "投标人须具备安全生产许可证")
    bundle = _bundle(
        response_content="部分响应",
        ledger_evidence=EvidenceLedger.to_prompt_block([
            EvidenceAtom(
                kind="安全生产许可证",
                grade="material",
                page=612,
                chunk_id=1,
                excerpt="安全生产许可证 编号(豫)JZ",
                source_requirement_ids=(1,),
            ),
        ]),
    )
    prompt = build_review_prompt(self=MagicMock(), pair=pair, evidence_bundle=bundle)
    assert "已核实材料" in prompt
    assert "第612页" in prompt
    assert "材料级" in prompt
    assert "来自要求#" not in prompt
    assert "#1" not in prompt


def test_pipeline_registers_ledger_presence_gate() -> None:
    names = [g.gate_name for g in build_pipeline_for("investigate")._gates]
    assert "LedgerPresenceGate" in names
    assert names.index("LedgerPresenceGate") > names.index("ClaimResponseConsistencyGate")
