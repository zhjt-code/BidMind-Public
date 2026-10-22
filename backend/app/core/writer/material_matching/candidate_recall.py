"""标书素材匹配 -- 候选召回。

职责: 以"完整语义命题"为单位，多源召回素材-章节候选(字面 + 可选向量语义)，
 经类型门禁过滤后按素材取 top_k，产出待 LLM 裁决的候选集。
不做: 不调用 LLM、不做最终绑定决策、不写库。

设计要点:
- 旧路径用 chunk 级最大余弦近邻直接绑定，通用证明极易误命中。此处语义召回改为对
 素材 proposition_text 与章节 demand_text(完整命题)整体编码后比对，而非任取 chunk。
- 向量编码通过可注入的 encoder(默认 safe_encode)完成；未提供 encoder 时仅走确定性
 字面召回，保证离线/无模型环境(含 golden 测试)可复现。
- 召回只负责"给出候选"，不形成绑定；相对分差与多章节收敛在 decision_policy 完成。
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable, Optional

import numpy as np

from app.core.writer.material_matching.contracts import (
    ChapterDemandProfile,
    MaterialChapterCandidate,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.type_gate import evaluate_pair_gate

Encoder = Callable[[list[str]], Awaitable[np.ndarray]]

_LITERAL_EXCLUSIVE = 0.92
_LITERAL_APPLICABLE_EXACT = 0.88
_LITERAL_APPLICABLE_PARTIAL = 0.72
_LITERAL_RESOLVED_LABEL = 0.6
_MIN_PARTIAL_LEN = 3


def _normalize_title(text: str) -> str:
    """归一化标题用于字面比对: 去空白/括号，小写。"""
    return re.sub(r"[（()）【】\[\]\s]", "", (text or "")).lower()


def _literal_recall(
    material: MaterialSemanticProfile, chapter: ChapterDemandProfile
) -> tuple[float, str]:
    """素材标签与章节标题的确定性字面召回分。"""
    title_norm = _normalize_title(chapter.title)
    if not title_norm:
        return 0.0, ""

    if material.exclusive_section:
        exclusive = _normalize_title(material.exclusive_section)
        if exclusive and (
            exclusive == title_norm
            or exclusive in title_norm
            or title_norm in exclusive
        ):
            return _LITERAL_EXCLUSIVE, "exclusive_section"

    best_score = 0.0
    best_source = ""
    for section in material.applicable_sections:
        section_norm = _normalize_title(section)
        if not section_norm:
            continue
        if section_norm == title_norm:
            if _LITERAL_APPLICABLE_EXACT > best_score:
                best_score, best_source = _LITERAL_APPLICABLE_EXACT, "applicable_exact"
        elif len(section_norm) >= _MIN_PARTIAL_LEN and (
            section_norm in title_norm or title_norm in section_norm
        ):
            if _LITERAL_APPLICABLE_PARTIAL > best_score:
                best_score, best_source = _LITERAL_APPLICABLE_PARTIAL, "applicable_partial"

    if material.resolved_label:
        resolved_norm = _normalize_title(material.resolved_label)
        if resolved_norm and resolved_norm == title_norm and _LITERAL_RESOLVED_LABEL > best_score:
            best_score, best_source = _LITERAL_RESOLVED_LABEL, "resolved_label"

    return best_score, best_source


async def _semantic_scores(
    materials: list[MaterialSemanticProfile],
    chapters: list[ChapterDemandProfile],
    encoder: Encoder,
) -> dict[tuple[int, int], float]:
    """对完整命题整体编码，返回 (material_id, node_id) -> 余弦相似度。"""
    material_texts = [(m.material_id, m.proposition_text) for m in materials if m.proposition_text]
    chapter_texts = [(c.node_id, c.demand_text) for c in chapters if c.demand_text]
    if not material_texts or not chapter_texts:
        return {}

    material_vectors = np.asarray(await encoder([text for _, text in material_texts]), dtype=float)
    chapter_vectors = np.asarray(await encoder([text for _, text in chapter_texts]), dtype=float)
    if material_vectors.ndim != 2 or chapter_vectors.ndim != 2:
        return {}

    material_norm = material_vectors / (
        np.linalg.norm(material_vectors, axis=1, keepdims=True) + 1e-12
    )
    chapter_norm = chapter_vectors / (
        np.linalg.norm(chapter_vectors, axis=1, keepdims=True) + 1e-12
    )
    similarity = material_norm @ chapter_norm.T

    scores: dict[tuple[int, int], float] = {}
    for i, (material_id, _) in enumerate(material_texts):
        for j, (node_id, _) in enumerate(chapter_texts):
            scores[(material_id, node_id)] = float(similarity[i, j])
    return scores


async def recall_candidates(
    materials: list[MaterialSemanticProfile],
    chapters: list[ChapterDemandProfile],
    *,
    top_k: int = 5,
    min_similarity: float = 0.55,
    encoder: Optional[Encoder] = None,
) -> list[MaterialChapterCandidate]:
    """多源召回素材->章节候选，过门禁后按素材取 top_k。

    Args:
        materials: 素材语义命题画像。
        chapters: 章节需求命题画像。
        top_k: 每个素材保留的候选上限。
        min_similarity: 语义召回的最低相似度阈值(字面命中不受此限)。
        encoder: 可注入的批量编码器(默认 None 表示跳过语义，仅走字面召回)。

    Returns:
        通过门禁(allow/review)的候选列表。
    """
    if not materials or not chapters:
        return []

    semantic = await _semantic_scores(materials, chapters, encoder) if encoder else {}

    candidates: list[MaterialChapterCandidate] = []
    for material in materials:
        scored: list[tuple[int, float, list[str], str, str]] = []
        for chapter in chapters:
            literal_score, literal_source = _literal_recall(material, chapter)
            semantic_score = semantic.get((material.material_id, chapter.node_id), 0.0)
            score = max(literal_score, semantic_score)
            if score < min_similarity:
                continue

            gate = evaluate_pair_gate(material, chapter)
            if gate.decision == "block":
                continue

            sources: list[str] = []
            if literal_source:
                sources.append(literal_source)
            if semantic_score >= min_similarity:
                sources.append("semantic")
            scored.append((chapter.node_id, round(score, 4), sources, gate.decision, gate.reason))

        scored.sort(key=lambda item: item[1], reverse=True)
        for node_id, score, sources, gate_decision, gate_reason in scored[:top_k]:
            candidates.append(
                MaterialChapterCandidate(
                    material_id=material.material_id,
                    node_id=node_id,
                    recall_score=score,
                    recall_sources=sources,
                    gate_decision=gate_decision,  # allow / review (block 已过滤)
                    gate_reason=gate_reason,
                )
            )
    return candidates


__all__ = ["recall_candidates", "Encoder"]
