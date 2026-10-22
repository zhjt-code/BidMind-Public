"""标书素材匹配 -- 编排管线。

职责: 素材->章节匹配的唯一编排入口，串联画像构建、类型门禁、语义召回、
      LLM 蕴含裁决与绑定决策，产出绑定提案。
不做: 不写库、不处理 HTTP、不做章节-约束裁决(Phase 2 职责)、不引入本地 reranker。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.core.writer.material_matching.candidate_recall import Encoder, recall_candidates
from app.core.writer.material_matching.contracts import (
    MaterialBindingProposal,
    MaterialMatchingRequest,
)
from app.core.writer.material_matching.decision_policy import select_binding_proposals
from app.core.writer.material_matching.pair_judge import judge_candidate_pairs
from app.core.writer.material_matching.profiles import (
    build_chapter_demand_profiles,
    build_material_profiles,
)
from app.core.writer.material_matching.unmatched_audit import (
    build_unmatched_material_proposals,
)


async def match_materials_to_chapters(
    request: MaterialMatchingRequest,
    *,
    llm_client: Any,
    encoder: Optional[Encoder] = None,
) -> list[MaterialBindingProposal]:
    """执行素材->章节匹配管线。

    Args:
        request: 已加载素材/章节等实体的匹配请求。
        llm_client: 具备 chat_with_validation 的 LLM 客户端(句对裁决)。
        encoder: 语义召回编码器(默认 None 表示仅字面召回；生产由 matcher 传入 safe_encode)。

    Returns:
        素材->章节绑定提案列表(material_to_node)。
    """
    material_profiles = build_material_profiles(request.materials)
    chapter_profiles = build_chapter_demand_profiles(request.nodes)
    if not material_profiles or not chapter_profiles:
        logger.info(
            "素材匹配管线跳过: materials={}, chapters={}",
            len(material_profiles),
            len(chapter_profiles),
        )
        return []

    candidates = await recall_candidates(material_profiles, chapter_profiles, encoder=encoder)
    if not candidates:
        logger.info("素材匹配管线无候选: project={}", request.project_id)
        return build_unmatched_material_proposals(
            materials=material_profiles,
            candidates=[],
            judge_outcome={},
            accepted_proposals=[],
        )

    judge_outcome = await judge_candidate_pairs(
        candidates, material_profiles, chapter_profiles, llm_client
    )
    proposals = select_binding_proposals(
        candidates,
        judge_outcome,
        material_profiles,
        chapter_profiles=chapter_profiles,
    )
    unmatched_proposals = build_unmatched_material_proposals(
        materials=material_profiles,
        candidates=candidates,
        judge_outcome=judge_outcome,
        accepted_proposals=proposals,
    )
    proposals.extend(unmatched_proposals)

    logger.info(
        "素材匹配管线完成: project={}, 候选={}, 提案={}, 未应用留痕={}",
        request.project_id,
        len(candidates),
        len(proposals),
        len(unmatched_proposals),
    )
    return proposals


__all__ = ["match_materials_to_chapters"]
