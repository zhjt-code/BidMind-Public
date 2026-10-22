"""标书素材匹配 -- 未应用素材留痕。

职责: 为最终0绑定的素材聚合候选否决原因，生成scope=orphan的待人工留痕提案。
不做: 不改变裁决/决策结果、不调用LLM、不写库。
"""

from __future__ import annotations

from app.core.writer.material_matching.contracts import (
    MaterialBindingProposal,
    MaterialChapterCandidate,
    MaterialPairDecision,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.pair_judge import PairJudgeOutcome

_MAX_REASON_CHARS = 300


def _clip_reason(reason: str) -> str:
    """把留痕原因裁剪到 writing_bindings.reason 可容纳范围。"""
    cleaned = " ".join((reason or "").split())
    if len(cleaned) <= _MAX_REASON_CHARS:
        return cleaned
    return cleaned[: _MAX_REASON_CHARS - 1] + "…"


def _format_decision_reason(
    candidate: MaterialChapterCandidate,
    decision: MaterialPairDecision | None,
) -> str:
    """把单个候选的裁决结果转为面向用户的未应用原因。"""
    if decision is None:
        return (
            f"候选章节{candidate.node_id}未返回有效裁决，"
            f"召回来源: {', '.join(candidate.recall_sources) or 'unknown'}"
        )
    prefix = "AI判定不适用" if decision.relation in {"unrelated", "conflict"} else "AI判定需复核"
    detail = decision.reason or decision.evidence_quote or "未提供具体理由"
    return f"{prefix}: {detail}"


def _reason_for_material(
    material_id: int,
    candidates: list[MaterialChapterCandidate],
    judge_outcome: PairJudgeOutcome,
) -> str:
    """汇总某素材未应用的最主要原因。"""
    material_candidates = [
        candidate for candidate in candidates if candidate.material_id == material_id
    ]
    if not material_candidates:
        return "素材未召回到合适章节候选，请人工确认是否需要分配到章节"

    material_candidates.sort(key=lambda candidate: candidate.recall_score, reverse=True)
    reasons = [
        _format_decision_reason(candidate, judge_outcome.get(candidate.pair_id))
        for candidate in material_candidates[:2]
    ]
    return _clip_reason("；".join(reasons))


def build_unmatched_material_proposals(
    *,
    materials: list[MaterialSemanticProfile],
    candidates: list[MaterialChapterCandidate],
    judge_outcome: PairJudgeOutcome,
    accepted_proposals: list[MaterialBindingProposal],
) -> list[MaterialBindingProposal]:
    """为最终没有有效章节绑定的素材生成 orphan 留痕提案。"""
    accepted_material_ids = {
        proposal.material_id
        for proposal in accepted_proposals
        if proposal.outline_node_id is not None
    }

    proposals: list[MaterialBindingProposal] = []
    for material in materials:
        if material.material_id in accepted_material_ids:
            continue
        proposals.append(
            MaterialBindingProposal(
                material_id=material.material_id,
                outline_node_id=None,
                confidence=0.0,
                reason=_reason_for_material(
                    material.material_id,
                    candidates,
                    judge_outcome,
                ),
                source="auto",
                needs_review=True,
                scope="orphan",
            )
        )
    return proposals


__all__ = ["build_unmatched_material_proposals"]
