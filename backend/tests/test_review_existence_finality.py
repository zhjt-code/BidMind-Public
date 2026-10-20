""" 审查侧存在性终态与一次重审。

覆盖风险点:
1. 一等注入含未响应路径;
2. deterministic_no_response 遇 ledger_presence 弃权;
3. 重审触发与 1 次封顶;
4. 终态五元组原子一致（禁 not/0/high 矛盾行）;
5. 重审翻正 vs 重审仍否认。
夹具: 准入证 / 授权委托书 / 社保缴费证明。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.ai.reviewer_parts.deterministic_no_response import evaluate_no_response_pair
from app.core.ai.reviewer_parts.evidence_ledger.contracts import EvidenceAtom, EvidenceLedger
from app.core.ai.reviewer_parts.evidence_prefetch import attach_ledger_evidence
from app.core.ai.reviewer_parts.existence_finalizer import (
    EXISTENCE_FLOOR_SCORE,
    collect_existence_conflicts,
    reconcile_existence_final_state,
)
from app.core.ai.reviewer_parts.existence_rereview import maybe_rereview_existence_denial
from app.core.retriever.evidence_coverage import EvidenceFacet
from app.core.review_contracts import ReviewEvidenceBundle
from app.schemas.review import ExtractedReviewResult


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


def _atom(kind: str, page: int, *, grade: str = "material", bid_part: str | None = "commercial") -> EvidenceAtom:
    return EvidenceAtom(
        kind=kind,
        grade=grade,
        page=page,
        chunk_id=page * 10,
        excerpt=f"{kind} 编号X 有效期至2028年",
        source_requirement_ids=(),
        document_id=1,
        bid_part=bid_part,
    )


def _extracted(
    *,
    comment: str,
    compliance_level: str = "not",
    score: int = 0,
    risk_level: str = "high",
) -> ExtractedReviewResult:
    return ExtractedReviewResult(
        requirement_id=2,
        category="qualification",
        requirement_summary="提供证明材料",
        response_summary="未提供相关材料",
        is_compliant=False,
        compliance_level=compliance_level,  # type: ignore[arg-type]
        score=score,
        comment=comment,
        risk_level=risk_level,  # type: ignore[arg-type]
    )


def _pair(description: str, *, has_response: bool = True):
    pair = MagicMock()
    pair.requirement_id = 2
    pair.category = "qualification"
    pair.requirement = SimpleNamespace(
        id=2,
        description=description,
        sub_requirements=None,
        keywords=None,
        project_id=9,
    )
    pair.is_mandatory = True
    pair.response_id = 20
    pair.has_response = has_response
    pair.response = SimpleNamespace(
        id=20,
        document_id=1,
        facet_states=None,
        content="" if not has_response else "部分响应",
        relevance_note=None,
        source_pages=None,
        completeness=None,
        missing_elements=None,
        evidence_type=None,
        evidence_anchor=None,
        linked_to=None,
    )
    pair._gate_ledger_atoms = []
    pair._existence_rereview_done = False
    return pair


class TestFirstClassInjection:
    def test_no_response_path_still_injects_material_atom(self):
        atom = _atom("授权委托书", 8)
        ledger = EvidenceLedger(atoms=(atom,))
        orchestrator = SimpleNamespace(_evidence_ledger=ledger)
        pair = _pair("投标人须提交授权委托书", has_response=False)
        bundle = _bundle(retrieval_chunk_pages=set())
        facets = (EvidenceFacet(key="授权委托书", query="授权委托书"),)
        attach_ledger_evidence(orchestrator, pair, facets, bundle)
        assert bundle.ledger_atoms
        assert "已核实材料" in bundle.ledger_evidence
        assert "授权委托书" in bundle.ledger_evidence
        assert pair._gate_ledger_atoms[0]["canonical_material_key"] == "授权委托书"


class TestDeterministicAbstain:
    def test_ledger_presence_flag_abstains(self):
        pair = _pair("投标人须提供近三年同类项目业绩证明材料及对应合同复印件。")
        pair.has_response = False
        pair.response.content = "[未找到响应]"
        bundle = _bundle(quality_flags=["ledger_presence:准入证"])
        outcome = evaluate_no_response_pair(pair, bundle)
        assert outcome.abstained is True


class TestConflictAndFinalizer:
    def test_admission_cert_atomic_tuple_no_contradiction(self):
        extracted = _extracted(comment="经检索未发现投标人准入证原件扫描件。")
        pair = _pair("投标人须具备专业准入证")
        evidence = {
            "ledger_atoms": [_atom("准入证", 40).to_dict()],
            "facet_states": [{
                "facet_key": "准入证",
                "canonical_material_key": "准入证",
                "presence": "found",
                "evidence_refs": [{"document_id": 1, "chunk_id": 400, "page": 40}],
            }],
        }
        revised, failure = reconcile_existence_final_state(extracted, pair, evidence)
        assert revised.compliance_level == "partially"
        assert revised.is_compliant is False
        assert revised.score >= EXISTENCE_FLOOR_SCORE
        assert revised.risk_level == "medium"
        assert failure == "needs_review"
        assert "第40页" in (revised.reconcile_note or "")
        assert revised.comment == extracted.comment

    def test_social_insurance_second_entity_same_floor(self):
        extracted = _extracted(comment="投标文件未提供社保缴费证明。")
        pair = _pair("投标人须提供社保缴费证明")
        evidence = {"ledger_atoms": [_atom("社保缴费证明", 90).to_dict()]}
        revised, failure = reconcile_existence_final_state(extracted, pair, evidence)
        assert revised.compliance_level != "not"
        assert revised.score >= EXISTENCE_FLOOR_SCORE
        assert failure == "needs_review"

    def test_rebuttal_does_not_floor(self):
        extracted = _extracted(
            comment="第40页准入证已过期，主体不符招标要求。",
            compliance_level="not",
            score=0,
            risk_level="high",
        )
        pair = _pair("投标人须具备专业准入证")
        evidence = {"ledger_atoms": [_atom("准入证", 40).to_dict()]}
        revised, failure = reconcile_existence_final_state(extracted, pair, evidence)
        assert failure is None
        assert revised.compliance_level == "not"
        assert revised.score == 0

    def test_all_found_and_model_confirms_no_intervention(self):
        extracted = _extracted(
            comment="第40页已提供准入证，有效期内，判定符合。",
            compliance_level="fully",
            score=95,
            risk_level="none",
        )
        pair = _pair("投标人须具备专业准入证")
        evidence = {"ledger_atoms": [_atom("准入证", 40).to_dict()]}
        revised, failure = reconcile_existence_final_state(extracted, pair, evidence)
        assert failure is None
        assert revised.score == 95
        assert revised.compliance_level == "fully"


class TestRereviewOnce:
    def test_triggers_once_and_caps(self):
        extracted = _extracted(comment="经检索未发现授权委托书原件扫描件。")
        flipped = _extracted(
            comment="第8页已见授权委托书，主体待核。",
            compliance_level="partially",
            score=60,
            risk_level="medium",
        )
        pair = _pair("投标人须提交授权委托书")
        bundle = _bundle(
            ledger_atoms=[_atom("授权委托书", 8)],
            ledger_evidence="## 本项目投标文件已核实材料（硬证据）\n- 授权委托书 / 第8页",
        )
        llm = AsyncMock(side_effect=[flipped, flipped])
        orch = SimpleNamespace(_call_llm_review=llm, item_timeout=5)

        async def _run():
            first = await maybe_rereview_existence_denial(
                orch, pair, extracted, bundle,
                prompt_rebuilder=lambda round: "BASE",
            )
            second = await maybe_rereview_existence_denial(
                orch, pair, first, bundle,
                prompt_rebuilder=lambda round: "BASE",
            )
            return first, second

        first, second = asyncio.run(_run())
        assert llm.await_count == 1
        assert first.compliance_level == "partially"
        assert second.comment == first.comment

    def test_still_denies_then_finalizer_floors(self):
        extracted = _extracted(comment="经检索未发现投标人准入证原件扫描件。")
        stubborn = _extracted(comment="仍未发现准入证扫描件。")
        pair = _pair("投标人须具备专业准入证")
        atom = _atom("准入证", 40)
        bundle = _bundle(ledger_atoms=[atom], ledger_evidence="- 准入证 / 第40页")
        orch = SimpleNamespace(
            _call_llm_review=AsyncMock(return_value=stubborn),
            item_timeout=5,
        )

        async def _run():
            return await maybe_rereview_existence_denial(
                orch, pair, extracted, bundle,
                prompt_rebuilder=lambda round: "BASE",
            )

        revised = asyncio.run(_run())
        final, failure = reconcile_existence_final_state(
            revised, pair, {"ledger_atoms": [atom.to_dict()]}
        )
        assert "未发现" in final.comment
        assert final.compliance_level == "partially"
        assert final.score >= EXISTENCE_FLOOR_SCORE
        assert failure == "needs_review"


class TestConflictMatcher:
    def test_unnamed_denial_still_conflicts_when_facet_hit(self):
        comment = "投标文件未提供相关材料，建议按不符合处理。"
        atoms = [_atom("准入证", 40).to_dict()]
        facets = (EvidenceFacet(key="准入证", query="准入证"),)
        hits = collect_existence_conflicts(comment, atoms, facets=facets)
        assert hits
        assert hits[0]["kind"] == "准入证"
