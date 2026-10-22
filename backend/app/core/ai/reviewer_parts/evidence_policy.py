"""审查证据策略 (工作包 2C)

职责: 审查证据充分性判定与分面保底的原文行选择
不做: 数据库查询、Prompt拼接、符合性判定

背景: 此前独立补搜仅在"否决项且来源页邻页没有任何图片文本"时触发, 任意无关
邻页图片都会抑制全局补搜, 非否决项永远无补救; 原文佐证固定按 role/similarity
取前3条, 同一分面的高分命中会挤掉其他分面的唯一证据。本模块把两类判定收口为
纯函数, 由 evidence_prefetch 装配层调用; 分面契约复用 evidence_coverage。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional, Sequence

from app.core.retriever.evidence_coverage import (
    EvidenceFacet,
    evaluate_facet_coverage,
    facet_hit,
    normalize_text,
)

# role 排序优先级(数字越小越靠前): 正文语义命中 > 二次深搜 > 目录导航 > 图片证据
# 与 evidence_prefetch 历史 _RAW_EVIDENCE_ROLE_RANK 同值。
RAW_EVIDENCE_ROLE_RANK: dict[str, int] = {"primary": 0, "deep": 1, "toc": 2, "image": 3}

# 判定为"证据不足"的完整性档位
_INSUFFICIENT_COMPLETENESS = frozenset({"partial", "form_only"})

# 无 chunk_id 时的降级去重键: 内容前缀长度(规范化空白后)
_CONTENT_DEDUPE_PREFIX = 60


@dataclass(frozen=True)
class EvidenceAdequacy:
    """一条匹配对在审查前的证据充分性快照(纯数据, 供触发判定与审计)。

 has_local_image_evidence 仅作审计记录, 刻意不参与补搜触发判定——
 "邻页存在图片文本"不能证明证据充分(根因一)。
 """

    has_response: bool
    is_reviewable: bool
    is_mandatory: bool
    has_source_pages: bool
    completeness: Optional[str]
    missing_elements: tuple[str, ...]
    evidence_anchor: Optional[str]
    facet_missing_keys: tuple[str, ...]
    has_local_image_evidence: bool
    is_prohibition: bool = False


def should_search_independently(state: EvidenceAdequacy) -> bool:
    """判定是否触发全局独立证据补搜。

    触发条件(任一): 响应缺失 / source_pages 为空 / completeness 为
    partial|form_only / missing_elements 非空 / 分面缺口非空 / 否决项缺
    evidence_anchor / 禁止项。非可审要求(is_reviewable=False)一律不搜。
    """
    if not state.is_reviewable:
        return False
    completeness = (state.completeness or "").strip().lower()
    return (
        not state.has_response
        or not state.has_source_pages
        or completeness in _INSUFFICIENT_COMPLETENESS
        or bool(state.missing_elements)
        or bool(state.facet_missing_keys)
        or (state.is_mandatory and not state.evidence_anchor)
        or state.is_prohibition
    )


def facet_missing_keys_for_rows(
    facets: Sequence[EvidenceFacet],
    rows: Sequence[dict[str, Any]],
) -> tuple[str, ...]:
    """用装配行(dict, 需含 content/matched_sub_query)评估分面缺口。"""
    if not facets:
        return ()
    probes = [
        SimpleNamespace(
            content=str(row.get("content") or ""),
            matched_sub_query=row.get("matched_sub_query"),
        )
        for row in rows
    ]
    return evaluate_facet_coverage(facets, probes).missing_keys


def _row_rank(row: dict[str, Any]) -> tuple[int, float]:
    return (
        RAW_EVIDENCE_ROLE_RANK.get(row.get("role") or "primary", 9),
        -float(row.get("similarity") or 0.0),
    )


def _row_identity(row: dict[str, Any]) -> tuple[str, Any]:
    """去重键: 优先 chunk_id, 缺失时退化为内容前缀(规范化空白)。"""
    chunk_id = row.get("chunk_id")
    if chunk_id is not None:
        return ("chunk", chunk_id)
    content = normalize_text(str(row.get("content") or ""))
    return ("content", content[:_CONTENT_DEDUPE_PREFIX])


def select_facet_covered_rows(
    rows: list[dict[str, Any]],
    facets: Sequence[EvidenceFacet],
    limit: int,
) -> list[dict[str, Any]]:
    """分面保底的原文行选择: 每分面先保一条最佳, 剩余额度按全局优先级补齐。

    第一轮分面内与第二轮全局补齐均按 (role_rank, -similarity) 稳定排序;
    分面归属优先 matched_sub_query 精确命中 facet.query, 再退化为 content
    字面命中 facet.key 探针(复用 evidence_coverage.facet_hit)。
    无分面时完全退化为全局排序截断(与历史行为一致)。
    """
    if limit <= 0 or not rows:
        return []

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for row in rows:
        identity = _row_identity(row)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(row)

    facet_tuple = tuple(facets)
    if not facet_tuple:
        return sorted(deduped, key=_row_rank)[:limit]

    buckets: dict[str, list[dict[str, Any]]] = {f.key: [] for f in facet_tuple}
    for row in deduped:
        sub_queries = {normalize_text(str(row.get("matched_sub_query") or ""))}
        sub_queries.discard("")
        contents = [normalize_text(str(row.get("content") or ""))]
        for facet in facet_tuple:
            if facet_hit(facet, sub_queries, contents):
                buckets[facet.key].append(row)
                break

    for bucket in buckets.values():
        bucket.sort(key=_row_rank)

    selected: list[dict[str, Any]] = []
    picked: set[tuple[str, Any]] = set()
    for facet in facet_tuple:
        if len(selected) >= limit:
            break
        bucket = buckets[facet.key]
        if bucket:
            selected.append(bucket[0])
            picked.add(_row_identity(bucket[0]))

    remainder = sorted(
        (row for row in deduped if _row_identity(row) not in picked),
        key=_row_rank,
    )
    for row in remainder:
        if len(selected) >= limit:
            break
        selected.append(row)
    return selected[:limit]
