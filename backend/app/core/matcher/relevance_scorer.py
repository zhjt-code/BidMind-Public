"""招标-投标相关性评分器

使用三维度加权算法计算招标要求与投标响应之间的相关性，
为 AI 审查阶段提供确定性的过滤信号。

评分维度:
    - 类型匹配 (0.55): 项目领域关键词交集命中率
    - 语义相似度 (0.30): bge-m3 嵌入余弦相似度
    - 领域关键词命中 (0.15): 全领域关键词丰富度

分数区间（与 prompts/review.py 对齐）:
    80+ = 高度相关
    50-79 = 部分相关
    0-49 = 响应不足
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

_TERMS_PATH = (
    Path(__file__).resolve().parent.parent
    / "extractor"
    / "tools"
    / "domain_terms.yaml"
)

TYPE_MATCH_WEIGHT: float = 0.55
SEMANTIC_SIM_WEIGHT: float = 0.30
DOMAIN_KEYWORD_WEIGHT: float = 0.15

# 降级时重新分配权重（语义不可用）
_DEGRADED_TYPE_WEIGHT: float = 0.65
_DEGRADED_DOMAIN_WEIGHT: float = 0.35


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RelevanceScore:
    """相关性评分结果。

    Attributes:
        score: 综合得分 0-100，语义降级时仍有值（通过重新分配权重计算）。
        type_match: 类型关键词匹配分 [0, 1]。
        semantic_sim: 语义余弦相似度 [0, 1]；嵌入模型不可用时为 0.0。
        domain_keyword_hit: 全领域关键词命中率 [0, 1]。
        trace: 调试追踪信息，包含 fallback 原因等。
    """

    score: int | None
    type_match: float
    semantic_sim: float
    domain_keyword_hit: float
    trace: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# YAML 加载（模块级单次缓存）
# --------------------------------------------------------------------------- #


@functools.lru_cache(maxsize=1)
def _load_relevance_domains() -> dict[str, list[str]]:
    """从 domain_terms.yaml 加载并缓存 relevance_domains 节。

    Returns:
        映射 {大写分类名: [关键词, ...]}；文件缺失或解析失败时返回空字典。
    """
    try:
        with _TERMS_PATH.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        raw: dict[str, Any] = data.get("relevance_domains", {})
        return {
            cat.upper(): list(cfg.get("keywords", []))
            for cat, cfg in raw.items()
            if isinstance(cfg, dict)
        }
    except Exception:
        return {}


def _get_category_keywords(category: str) -> list[str]:
    """返回指定分类的领域关键词列表。

    Args:
        category: 分类代码（大小写不敏感），如 PERF / QUAL / STAFF / COMM / TECH。

    Returns:
        关键词列表；未知分类返回空列表。
    """
    return _load_relevance_domains().get(category.upper(), [])


def _get_all_keywords() -> list[str]:
    """返回所有分类的领域关键词（去重，保持插入顺序）。

    Returns:
        全量关键词列表。
    """
    seen: set[str] = set()
    result: list[str] = []
    for keywords in _load_relevance_domains().values():
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
    return result


# --------------------------------------------------------------------------- #
# 三维度计算函数
# --------------------------------------------------------------------------- #


def _compute_type_match(
    requirement_text: str,
    response_text: str,
    category: str,
) -> float:
    """计算类型匹配分。

    统计目标分类的关键词中，**同时**出现在招标要求文本和投标响应文本的数量
    （交集语义）。未知分类（关键词列表为空）返回中性值 0.5。

    Args:
        requirement_text: 招标要求文本。
        response_text: 投标响应文本。
        category: 分类代码（大小写不敏感）。

    Returns:
        float in [0, 1]。
    """
    keywords = _get_category_keywords(category)
    total = len(keywords)
    if total == 0:
        return 0.5  # 未知分类，返回中性默认值

    matched = sum(
        1
        for kw in keywords
        if kw in requirement_text and kw in response_text
    )
    return max(0.0, min(1.0, matched / total))


def _compute_semantic_sim(
    text_a: str,
    text_b: str,
) -> tuple[float, dict[str, Any]]:
    """计算两段文本的 bge-m3 嵌入余弦相似度。

    当嵌入模型不可用时（导入失败、模型加载失败等）优雅降级，
    返回 0.0 并在追踪字典中记录 fallback 原因。

    Args:
        text_a: 第一段文本。
        text_b: 第二段文本。

    Returns:
        (similarity, trace)，其中 similarity in [0, 1]，trace 含可选的
        "fallback" 和 "error" 字段。
    """
    try:
        from app.core.vectorizer import get_embedding_model  # noqa: PLC0415

        model = get_embedding_model()
        embeddings = model.encode([text_a, text_b])
        norm_a = np.linalg.norm(embeddings[0])
        norm_b = np.linalg.norm(embeddings[1])
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0, {"fallback": "zero_norm_vector"}
        sim = float(
            np.dot(embeddings[0], embeddings[1]) / (norm_a * norm_b)
        )
        return max(0.0, min(1.0, sim)), {}
    except Exception as exc:  # noqa: BLE001
        return 0.0, {"fallback": "embedding_unavailable", "error": str(exc)}


def _compute_domain_keyword_hit(
    requirement_text: str,
    response_text: str,
) -> float:
    """计算全领域关键词命中率。

    遍历所有分类的关键词，检查每个关键词是否出现在招标要求或投标响应的
    **任意一方**（并集语义），衡量文本的"领域丰富程度"。

    Args:
        requirement_text: 招标要求文本。
        response_text: 投标响应文本。

    Returns:
        float in [0, 1]；无关键词定义时返回 0.0。
    """
    all_keywords = _get_all_keywords()
    total = len(all_keywords)
    if total == 0:
        return 0.0

    hits = sum(
        1
        for kw in all_keywords
        if kw in requirement_text or kw in response_text
    )
    return max(0.0, min(1.0, hits / total))


# --------------------------------------------------------------------------- #
# 公开 API
# --------------------------------------------------------------------------- #


def score_relevance(
    requirement_text: str,
    response_text: str,
    category: str,
) -> RelevanceScore:
    """计算招标要求与投标响应的相关性得分。

    综合三个维度给出 0-100 的整数分：

    正常路径（嵌入模型可用）::

        raw = 0.55 × type_match + 0.30 × semantic_sim + 0.15 × domain_kw_hit

    降级路径（嵌入模型不可用，semantic_sim == 0 且 trace 含 "fallback"）::

        raw = 0.65 × type_match + 0.35 × domain_kw_hit

    Args:
        requirement_text: 招标要求文本（明文）。
        response_text: 投标响应文本（明文）。
        category: 要求分类，支持 PERF / QUAL / STAFF / COMM / TECH（大小写不敏感）。
                  未知分类不报错，type_match 以 0.5 中性值计算。

    Returns:
        RelevanceScore 实例，score 范围 [0, 100]，并附带各维度分值和追踪信息。
        分数区间：80+ 高度相关，50-79 部分相关，0-49 响应不足。
    """
    type_match = _compute_type_match(requirement_text, response_text, category)
    semantic_sim, sem_trace = _compute_semantic_sim(requirement_text, response_text)
    domain_keyword_hit = _compute_domain_keyword_hit(requirement_text, response_text)

    trace: dict[str, Any] = dict(sem_trace)

    if semantic_sim == 0.0 and sem_trace.get("fallback"):
        raw = _DEGRADED_TYPE_WEIGHT * type_match + _DEGRADED_DOMAIN_WEIGHT * domain_keyword_hit
        trace["weight_redistribution"] = "semantic_unavailable"
    else:
        raw = (
            TYPE_MATCH_WEIGHT * type_match
            + SEMANTIC_SIM_WEIGHT * semantic_sim
            + DOMAIN_KEYWORD_WEIGHT * domain_keyword_hit
        )

    score = max(0, min(100, round(raw * 100)))

    return RelevanceScore(
        score=score,
        type_match=type_match,
        semantic_sim=semantic_sim,
        domain_keyword_hit=domain_keyword_hit,
        trace=trace,
    )
