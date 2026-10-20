"""
职责: 检出「存在性否认 ∧ 库存材料级强命中同一材料」冲突并驱动一次硬证据置顶重审
不做: 检索、改写意见、二次重试
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

from app.core.ai.reviewer_parts.existence_finalizer import (
    collect_existence_conflicts,
    format_conflict_location_note,
)
from app.core.ai.reviewer_parts.scoring_rubric import expects_rule_assessments
from app.core.review_contracts import parse_json_list

if TYPE_CHECKING:
    from app.core.ai.llm_retry_resume import LlmRetryResumeState
    from app.core.ai.reviewer import ReviewOrchestrator
    from app.core.matcher.requirement_matcher import MatchedPair
    from app.core.review_contracts import ReviewEvidenceBundle
    from app.schemas.review import ExtractedReviewResult


def build_hard_evidence_prefix(pair: "MatchedPair", evidence_bundle: "ReviewEvidenceBundle") -> str:
    """硬证据置顶块；存在性已定，模型只判适用性。"""
    ledger = str(getattr(evidence_bundle, "ledger_evidence", "") or "").strip()
    atoms = [
        atom.to_dict() if hasattr(atom, "to_dict") else dict(atom)
        for atom in (getattr(evidence_bundle, "ledger_atoms", None) or ())
        if (getattr(atom, "grade", None) or (atom.get("grade") if isinstance(atom, dict) else ""))
        == "material"
    ]
    location = format_conflict_location_note(atoms) if atoms else ""
    parts = [
        "## 硬证据（存在性已核实，禁止写未提供/未发现/缺失）",
        "下列材料已在本项目投标文件中核实存在。存在性已定，只判适用性"
        "（主体/有效期/等级/专业类别）。如认为不适用必须点名反驳理由。",
    ]
    if location:
        parts.append(location)
    if ledger:
        parts.append(ledger)
    return "\n".join(parts)


async def maybe_rereview_existence_denial(
    orchestrator: "ReviewOrchestrator",
    pair: "MatchedPair",
    extracted: "ExtractedReviewResult",
    evidence_bundle: "ReviewEvidenceBundle",
    *,
    prompt_rebuilder: Callable[[int], str],
    resume_state: Optional["LlmRetryResumeState"] = None,
) -> "ExtractedReviewResult":
    """冲突时带硬证据置顶重审一次；enforce 直接采用重审结果。"""
    if getattr(pair, "_existence_rereview_done", False):
        return extracted
    comment = str(getattr(extracted, "comment", "") or "")
    from app.core.retriever.evidence_coverage import build_evidence_facets

    atoms = [
        atom.to_dict() if hasattr(atom, "to_dict") else dict(atom)
        for atom in (getattr(evidence_bundle, "ledger_atoms", None) or ())
    ]
    if not atoms:
        atoms = list(getattr(pair, "_gate_ledger_atoms", None) or ())
    requirement = getattr(pair, "requirement", None)
    facets = ()
    if requirement is not None:
        facets = build_evidence_facets(
            str(getattr(requirement, "description", "") or ""),
            parse_json_list(getattr(requirement, "sub_requirements", None)),
            keywords=parse_json_list(getattr(requirement, "keywords", None)),
        )
    response = getattr(pair, "response", None)
    facet_states = list(getattr(response, "facet_states", None) or ()) if response else ()
    conflicts = collect_existence_conflicts(
        comment, atoms, facet_states=facet_states, facets=facets
    )
    if not conflicts:
        return extracted

    try:
        pair._existence_rereview_done = True  # type: ignore[attr-defined]
    except Exception:
        pass

    prefix = build_hard_evidence_prefix(pair, evidence_bundle)

    def rebuild(reduction_round: int) -> str:
        return prefix + "\n\n" + prompt_rebuilder(reduction_round)

    timeout = float(getattr(orchestrator, "item_timeout", 120) or 120)
    try:
        revised = await asyncio.wait_for(
            orchestrator._call_llm_review(
                rebuild(0),
                prompt_rebuilder=rebuild,
                operation_label=f"要求 {pair.requirement_id} 存在性重审",
                expects_nested_array_output=expects_rule_assessments(pair),
                pair=pair,
                node_key=(
                    "bid_review.review_item" if pair.has_response
                    else "bid_review.review_no_response"
                ),
                resume_state=resume_state,
            ),
            timeout=timeout,
        )
    except Exception as err:
        logger.debug("存在性重审降级: {}", err)
        return extracted
    return revised or extracted
