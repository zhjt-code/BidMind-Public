"""标书素材匹配 -- 绑定决策策略。

职责: 综合 LLM 蕴含裁决与召回信号，把候选收敛为最终素材->章节绑定提案。
不做: 不召回、不调用 LLM、不写库。

设计要点:
- 仅 supports/partial 产生绑定；unrelated/conflict 及"无裁决"一律丢弃(保守)。
- 画像不完整(profile_complete=False)的素材禁止"纯语义单路"放行: 必须有字面/类型旁证。
- 章节画像 degraded(profile_complete=False)时同样禁止纯语义单路，脱离标题-only 静默降级。
- 相对分差剪枝: 以该素材最高分为基准，丢弃明显更低的 partial。
- 每素材至多 max_nodes 个绑定，partial 标记 needs_review 供人工确认。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.core.writer.material_matching.contracts import (
    ChapterDemandProfile,
    MaterialBindingProposal,
    MaterialChapterCandidate,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.pair_judge import PairJudgeOutcome

# 只有 supports/partial 允许成边；数值越大优先级越高。
_RELATION_RANK: dict[str, int] = {"supports": 2, "partial": 1}
_SUPPORTS_RANK = 2


@dataclass(frozen=True, slots=True)
class BindingSelectionInput:
    """select_binding_proposals 入参聚合（复杂度护栏: 参数>6 时收敛）。

    职责: 承载决策所需的候选/裁决/双边画像与裁剪参数。
    不做: 不做裁决、不写库。
    """

    candidates: list[MaterialChapterCandidate]
    judge_outcome: PairJudgeOutcome
    material_profiles: list[MaterialSemanticProfile]
    chapter_profiles: list[ChapterDemandProfile] | None = None
    max_nodes: int = 3
    relative_margin: float = 0.15


def _has_non_semantic_evidence(candidate: MaterialChapterCandidate) -> bool:
    """候选是否存在语义之外的旁证(字面/排他/标签命中)。"""
    return any(source != "semantic" for source in candidate.recall_sources)


def select_binding_proposals(
    candidates: list[MaterialChapterCandidate] | BindingSelectionInput,
    judge_outcome: PairJudgeOutcome | None = None,
    material_profiles: list[MaterialSemanticProfile] | None = None,
    *,
    chapter_profiles: list[ChapterDemandProfile] | None = None,
    max_nodes: int = 3,
    relative_margin: float = 0.15,
) -> list[MaterialBindingProposal]:
    """把候选与裁决收敛为绑定提案。

    兼容两种调用:
    1. 位置参数 (candidates, judge_outcome, material_profiles, ...)
    2. BindingSelectionInput 聚合入参
    """
    if isinstance(candidates, BindingSelectionInput):
        selection = candidates
    else:
        if judge_outcome is None or material_profiles is None:
            raise TypeError(
                "select_binding_proposals 需要 judge_outcome 与 material_profiles"
            )
        selection = BindingSelectionInput(
            candidates=candidates,
            judge_outcome=judge_outcome,
            material_profiles=material_profiles,
            chapter_profiles=chapter_profiles,
            max_nodes=max_nodes,
            relative_margin=relative_margin,
        )

    profile_by_id = {m.material_id: m for m in selection.material_profiles}
    chapter_by_id = {
        c.node_id: c for c in (selection.chapter_profiles or [])
    }
    grouped: dict[int, list[MaterialChapterCandidate]] = defaultdict(list)
    for candidate in selection.candidates:
        grouped[candidate.material_id].append(candidate)

    proposals: list[MaterialBindingProposal] = []
    for material_id, material_candidates in grouped.items():
        material = profile_by_id.get(material_id)
        material_complete = bool(material.profile_complete) if material else False

        accepted: list[tuple[int, float, MaterialChapterCandidate, str]] = []
        for candidate in material_candidates:
            decision = selection.judge_outcome.get(candidate.pair_id)
            if decision is None:
                continue  # 无裁决: 保守丢弃
            rank = _RELATION_RANK.get(decision.relation, 0)
            if rank == 0:
                continue  # unrelated / conflict

            chapter = chapter_by_id.get(candidate.node_id)
            # 未传入 chapter_profiles 时不额外拦截（向后兼容旧调用）
            chapter_complete = (
                True if chapter is None else bool(chapter.profile_complete)
            )

            # 素材或章节画像不完整 + 纯语义单路 -> 丢弃(需字面/类型旁证)
            if (
                (not material_complete or not chapter_complete)
                and not _has_non_semantic_evidence(candidate)
            ):
                continue
            accepted.append((rank, decision.confidence, candidate, decision.reason))

        if not accepted:
            continue

        accepted.sort(
            key=lambda item: (item[0], item[1], item[2].recall_score),
            reverse=True,
        )
        top_score = accepted[0][1]

        selected = 0
        for rank, confidence, candidate, reason in accepted:
            if selected >= selection.max_nodes:
                break
            # 相对分差剪枝: partial 明显低于最高分时丢弃
            if (
                rank < _SUPPORTS_RANK
                and confidence < top_score - selection.relative_margin
            ):
                continue
            proposals.append(
                MaterialBindingProposal(
                    material_id=candidate.material_id,
                    outline_node_id=candidate.node_id,
                    confidence=round(confidence, 3),
                    reason=reason or "素材-章节蕴含裁决通过",
                    source="auto",
                    needs_review=rank < _SUPPORTS_RANK,
                )
            )
            selected += 1

    return proposals


__all__ = ["select_binding_proposals", "BindingSelectionInput"]
