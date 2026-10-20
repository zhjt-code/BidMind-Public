"""评分项 LLM 兜底忠实性护栏。

职责: 校验 LLM 兜底评分项与评分表 chunk 原文的文本忠实性并生成元数据标记。
不做: 不解析表格、不调用 LLM/DB、不决定评分项取舍。

检测算法: 对 item.description + item.score_rules 合并文本做字符 bigram 集合计算，
与 source_text 的 bigram 集合做包含率 (item bigram 命中率)。
- 包含率 >= FIDELITY_THRESHOLD → flag='ok'（文本充分见于原文）
- 包含率 < FIDELITY_THRESHOLD → flag='rewritten'（LLM 改写/压缩疑似）
- 空文本/极短文本 → flag='needs_review'（无法判断，保守标记）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Sequence

# 最小 bigram 包含率阈值: item_bigrams ∩ source_bigrams / item_bigrams_count
# 低于此值视为 LLM 改写/压缩。实测: 原文忠实提取 >= 0.5, 改写通常 < 0.25。
_FIDELITY_THRESHOLD: float = 0.30

# 最小有效文本长度(字符): 少于此值 bigram 集合过小，判定不可靠
_MIN_TEXT_LENGTH: int = 4

FidelityFlag = Literal["ok", "rewritten", "needs_review"]
FidelityKey = tuple[str, str, str]


@dataclass(frozen=True)
class FidelityAnnotation:
    """单条评分项忠实性标注结果。

    flag:          ok / rewritten / needs_review
    note:          可展示说明（flag='ok' 时为空字符串）
    overlap_score: item bigram 包含率 [0, 1]
    """

    flag: FidelityFlag
    note: str
    overlap_score: float


def _normalize_text(text: str) -> str:
    """去全部空白字符并转小写，用于 bigram 比对。"""
    return re.sub(r"\s+", "", str(text or "")).lower()


def _char_bigrams(text: str) -> frozenset[str]:
    """字符级 bigram 集合。文本过短时返回空集。"""
    norm = _normalize_text(text)
    if len(norm) < 2:
        return frozenset()
    return frozenset(norm[i : i + 2] for i in range(len(norm) - 1))


def _containment_rate(item_bigrams: frozenset[str], source_bigrams: frozenset[str]) -> float:
    """计算 item_bigrams 在 source_bigrams 中的包含率: |intersection| / |item_bigrams|。

    语义: item 文本的多少比例在原文中出现。包含率高 → 原文忠实提取; 低 → LLM 改写。
    """
    if not item_bigrams or not source_bigrams:
        return 0.0
    matched = len(item_bigrams & source_bigrams)
    return matched / len(item_bigrams)


def _build_item_text(description: str, score_rules: list[str]) -> str:
    """合并 description 与 score_rules 为单一比对文本。"""
    parts = [description] + list(score_rules or [])
    return " ".join(p for p in parts if p)


def _score_key(raw: Any) -> str:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return ""
    return f"{value:.4f}"


def build_fidelity_key(item: Any) -> FidelityKey:
    """构造评分项忠实性标注键，避免仅用描述前缀导致跨 chunk 错挂。"""
    return (
        str(getattr(item, "score_item_name", "") or ""),
        _score_key(getattr(item, "score_weight", None)),
        str(getattr(item, "description", "") or ""),
    )


def check_item_fidelity(
    description: str,
    score_rules: list[str],
    source_text: str,
) -> FidelityAnnotation:
    """检查单条评分项与原文的忠实性。

    参数:
        description:  评分项描述文本
        score_rules:  得分/分档规则列表（可为空）
        source_text:  评分表 chunk 原始文本

    返回 FidelityAnnotation，flag 含义:
        ok           → 充分见于原文，可信
        rewritten    → 与原文重合率低，LLM 可能改写
        needs_review → 文本太短或原文为空，无法判断
    """
    # 保守兜底: 空描述或空原文 → needs_review
    if not _normalize_text(description):
        return FidelityAnnotation(
            flag="needs_review",
            note="description 为空，无法比对忠实性",
            overlap_score=0.0,
        )
    if not _normalize_text(source_text):
        return FidelityAnnotation(
            flag="needs_review",
            note="source_text 为空，无法比对忠实性",
            overlap_score=0.0,
        )

    item_text = _build_item_text(description, score_rules)
    item_bigrams = _char_bigrams(item_text)

    # 文本过短 bigram 集合不可信 → needs_review
    if len(item_bigrams) < _MIN_TEXT_LENGTH // 2:
        return FidelityAnnotation(
            flag="needs_review",
            note="评分项文本过短（bigram 数量不足），保守标记待复核",
            overlap_score=0.0,
        )

    source_bigrams = _char_bigrams(source_text)
    score = _containment_rate(item_bigrams, source_bigrams)
    # 收敛到 [0, 1]
    score = max(0.0, min(1.0, score))

    if score >= _FIDELITY_THRESHOLD:
        return FidelityAnnotation(flag="ok", note="", overlap_score=score)

    return FidelityAnnotation(
        flag="rewritten",
        note=f"描述与原文重合率低（{score:.2f}<{_FIDELITY_THRESHOLD}），疑似LLM改写/压缩，建议人工核查",
        overlap_score=score,
    )


def annotate_scoring_fidelity(
    items: Sequence,
    source_text: str,
) -> dict[FidelityKey, FidelityAnnotation]:
    """批量标注评分项忠实性，返回 sidecar dict。

    参数:
        items:       评分项序列（任何带 description/score_rules 属性的对象）
        source_text: 评分表 chunk 原始文本

    返回:
        sidecar dict，键为 (score_item_name, score_weight, description) 结构键，
        值为 FidelityAnnotation。调用方在 _save_items 时用同一结构键查找。
    """
    sidecar: dict[FidelityKey, FidelityAnnotation] = {}
    for item in items:
        description = getattr(item, "description", "") or ""
        score_rules = getattr(item, "score_rules", []) or []
        annotation = check_item_fidelity(description, list(score_rules), source_text)
        sidecar[build_fidelity_key(item)] = annotation
    return sidecar
