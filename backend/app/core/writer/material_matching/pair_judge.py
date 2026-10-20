"""标书素材匹配 -- LLM 句对蕴含裁决。

职责: 把召回候选按素材聚合分批，交 LLM 逐对裁决蕴含关系(supports/partial/
 unrelated/conflict)，回填并校验 pair_id 完整性。
不做: 不做召回、不做最终绑定决策、不写库。

设计要点:
- 复用现有 LLMClient.chat_with_validation + PromptRegistry(material_pair_judge 节点)，
 不引入本地 reranker；Registry 接入在本模块完成，不污染巨型的 material_matcher.py。
- 同一素材的候选放同一批，便于 LLM 在多个相关章节间"择最精确"，其余判 partial。
- 裁决失败的批次跳过而非抛出，交由 decision_policy 对缺裁决候选保守处理(不放行)。
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from loguru import logger

from app.core.ai.prompt_registry import call_with_fallback, resolve_runtime_params
from app.core.writer.material_matching.contracts import (
    ChapterDemandProfile,
    MaterialChapterCandidate,
    MaterialPairDecision,
    MaterialPairJudgeBatch,
    MaterialSemanticProfile,
)
from app.core.writer.prompts.material_pair_judge import (
    get_material_pair_judge_prompt as _code_pair_judge_prompt,
)

NODE_KEY = "writing.material_pair_judge"
_DEFAULT_TEMPERATURE = 0.0
_DEFAULT_MAX_TOKENS = 3000
_PAIR_JUDGE_BATCH_CONCURRENCY = 3

# pair_id -> 裁决。缺裁决的候选不在字典内(decision_policy 视为不放行)。
PairJudgeOutcome = dict[str, MaterialPairDecision]


def _resolve_pair_judge_prompt(pairs_json: str) -> tuple[str, str]:
    """Registry 优先 + 代码默认兜底的裁决 Prompt。"""
    return call_with_fallback(NODE_KEY, _code_pair_judge_prompt, pairs_json=pairs_json)


def _build_pair_payload(
    candidate: MaterialChapterCandidate,
    material: MaterialSemanticProfile,
    chapter: ChapterDemandProfile,
) -> dict:
    """构造单个候选对的裁决输入载荷。"""
    return {
        "pair_id": candidate.pair_id,
        "material": {
            "title": material.title,
            "business_type": material.business_type or "",
            "resolved_label": material.resolved_label or "",
            "purpose": material.bid_purpose,
            "proposition": material.proposition_text,
        },
        "chapter": {
            "title": chapter.title,
            "role": chapter.semantic_role,
            "expects": chapter.expects,
            "rejects": chapter.rejects,
        },
    }


def _batch_candidates(
    candidates: list[MaterialChapterCandidate], batch_size: int
) -> list[list[MaterialChapterCandidate]]:
    """按素材聚合后贪心打包为批，避免同素材候选被拆到不同批。"""
    grouped: dict[int, list[MaterialChapterCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.material_id].append(candidate)

    batches: list[list[MaterialChapterCandidate]] = []
    current: list[MaterialChapterCandidate] = []
    for group in grouped.values():
        if current and len(current) + len(group) > batch_size:
            batches.append(current)
            current = []
        current.extend(group)
    if current:
        batches.append(current)
    return batches


async def _judge_one_batch(
    batch: list[MaterialChapterCandidate],
    material_by_id: dict[int, MaterialSemanticProfile],
    chapter_by_id: dict[int, ChapterDemandProfile],
    llm_client: Any,
) -> PairJudgeOutcome:
    """裁决单批候选对；失败返回空 dict，不抛出。"""
    payload: list[dict] = []
    batch_pair_ids: set[str] = set()
    for candidate in batch:
        material = material_by_id.get(candidate.material_id)
        chapter = chapter_by_id.get(candidate.node_id)
        if material is None or chapter is None:
            continue
        payload.append(_build_pair_payload(candidate, material, chapter))
        batch_pair_ids.add(candidate.pair_id)
    if not payload:
        return {}

    pairs_json = json.dumps(payload, ensure_ascii=False)
    system_msg, user_msg = _resolve_pair_judge_prompt(pairs_json)
    temperature, max_tokens = resolve_runtime_params(
        NODE_KEY, _DEFAULT_TEMPERATURE, _DEFAULT_MAX_TOKENS
    )

    try:
        result = await llm_client.chat_with_validation(
            user_message=user_msg,
            response_model=MaterialPairJudgeBatch,
            system_message=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 裁决失败不阻断整体，交下游保守处理
        logger.warning(
            "素材句对裁决批次失败, 跳过该批 {} 对: {}", len(batch_pair_ids), exc
        )
        return {}

    decisions: PairJudgeOutcome = {}
    for decision in result.decisions:
        if decision.pair_id in batch_pair_ids:
            decisions[decision.pair_id] = decision
    return decisions


async def judge_candidate_pairs(
    candidates: list[MaterialChapterCandidate],
    material_profiles: list[MaterialSemanticProfile],
    chapter_profiles: list[ChapterDemandProfile],
    llm_client: Any,
    *,
    batch_size: int = 12,
) -> PairJudgeOutcome:
    """对候选对批量做 LLM 蕴含裁决，返回 pair_id -> 裁决。

    Args:
        candidates: 召回产出的候选对。
        material_profiles: 素材语义命题(供构造裁决输入)。
        chapter_profiles: 章节需求命题。
        llm_client: 具备 chat_with_validation 的 LLM 客户端。
        batch_size: 每批最多裁决的候选对数。

    Returns:
        pair_id -> MaterialPairDecision；裁决失败或缺失的候选不在结果中。
    """
    if not candidates or llm_client is None:
        return {}

    material_by_id = {m.material_id: m for m in material_profiles}
    chapter_by_id = {c.node_id: c for c in chapter_profiles}
    batches = _batch_candidates(candidates, batch_size)
    semaphore = asyncio.Semaphore(max(_PAIR_JUDGE_BATCH_CONCURRENCY, 1))

    async def _run_batch(
        batch: list[MaterialChapterCandidate],
    ) -> PairJudgeOutcome:
        async with semaphore:
            return await _judge_one_batch(
                batch, material_by_id, chapter_by_id, llm_client,
            )

    batch_outcomes = await asyncio.gather(
        *(_run_batch(batch) for batch in batches)
    )
    decisions: PairJudgeOutcome = {}
    for outcome in batch_outcomes:
        decisions.update(outcome)

    logger.info(
        "素材句对裁决完成: 候选={}, 裁决命中={}", len(candidates), len(decisions)
    )
    return decisions


__all__ = ["judge_candidate_pairs", "PairJudgeOutcome", "NODE_KEY"]
