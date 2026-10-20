"""证据分面覆盖契约 

职责: 把招标要求及其子要求映射为可检索分面, 并提供证据单调合并、
 分面覆盖评估与分面保底选择的纯函数
不做: 数据库查询、LLM 调用、ORM 持久化、符合性判断

背景: 此前完整性判断只看 cert/perf/social/aux 四个词桶的"是否出现",
多实体(三名安全员)、并列子面(防护用品/药品/专项费用)只要命中其一即被
视为完整; 深搜第二轮又只消费新增证据, 可把首轮成功覆盖成未找到。
本模块以"分面"为最小判断单位, 供提取补搜触发与审查原文选择共同消费。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from app.core.retriever.semantic_retriever import SearchResult

# 分面数量上限(与 QueryDecomposer.MAX_SUB_QUERIES 对齐, 防止病态长句爆炸)
MAX_FACETS = 20

_WS = re.compile(r"\s+")
# 泛指主体前缀: decompose 产出"主体 客体"时, 泛主体不构成分面区分度
_GENERIC_PREFIXES = frozenset({
    "投标人", "招标人", "投标方", "招标方", "甲方", "乙方",
    "供应商", "应答人", "采购人", "中标人", "委托方", "受托方",
    "投标文件", "响应文件", "招标文件",
})
# 客体尾部的"等材料/等资料"类收尾词, 剥离后主探针更易字面命中
_TAIL_NOISE = re.compile(r"等?(材料|资料|文件|证明材料)?$")


def _normalize(text: str) -> str:
    return _WS.sub("", text or "")


@dataclass(frozen=True)
class EvidenceFacet:
    """单个可检索分面: key 为稳定判定键, query 为检索查询文本。"""

    key: str
    query: str


@dataclass(frozen=True)
class FacetCoverage:
    """分面覆盖评估结果(全部使用 facet.key)。"""

    expected_keys: tuple[str, ...]
    covered_keys: tuple[str, ...]
    missing_keys: tuple[str, ...]


def _facet_key_from_query(query: str) -> str:
    """从子查询构造分面键: 剥离泛主体前缀与尾部收尾词。"""
    parts = [p for p in (query or "").split() if p]
    if len(parts) >= 2 and parts[0] in _GENERIC_PREFIXES:
        parts = parts[1:]
    key = _normalize("".join(parts))
    stripped = _TAIL_NOISE.sub("", key)
    return stripped if len(stripped) >= 2 else key


def _literal_probes(key: str) -> tuple[str, ...]:
    """分面键的内容字面探针: 整短语优先, 长键补前缀探针(前缀区分度高于泛化后缀)。"""
    compact = _normalize(key)
    if not compact:
        return ()
    if len(compact) <= 6:
        return (compact,)
    return (compact, compact[:6])


def build_evidence_facets(
    description: str,
    sub_requirements: Sequence[str],
    keywords: Sequence[str] = (),
) -> tuple[EvidenceFacet, ...]:
    """把要求描述、LLM 关键词与人工确认子要求映射为去重分面序列。

 描述侧复用 QueryDecomposer 的复合分解(主体x客体/并列客体),
 整句本身不构成分面(无区分度); 子要求逐条成为专属分面。

 词面多源化:
 - `keywords`(招标要求提取阶段 LLM 产出)是最高质量语义单元, 直接成一等分面;
 此前该列整条检索链无人消费, 只用于报告展示。
 - `sub_requirements` 常是复合拆分产出的 ASCII 子面标签(如 enterprise_perf),
 原样当分面查询无字面区分度; 翻译为证据触发词后再成分面。
 """
    from app.core.retriever.facet_vocab import translate_facet_label
    from app.core.retriever.query_decomposer import get_query_decomposer

    facets: list[EvidenceFacet] = []
    seen: set[str] = set()
    desc_prefix = (description or "")[:30]

    def _add(key: str, query: str) -> None:
        if not key or len(key) < 2 or key in seen:
            return
        seen.add(key)
        facets.append(EvidenceFacet(key=key, query=(query or "").strip() or key))

    normalized_desc = _normalize(description or "")
    for query in get_query_decomposer().decompose(description or ""):
        if _normalize(query) == normalized_desc:
            continue
        _add(_facet_key_from_query(query), query)

    for kw in keywords or []:
        if isinstance(kw, str) and kw.strip():
            _add(_normalize(kw), f"{desc_prefix} {kw.strip()}".strip())

    for sub in sub_requirements or []:
        if not isinstance(sub, str) or not sub.strip():
            continue
        trigger_words = translate_facet_label(sub)
        if trigger_words:
            for word in trigger_words:
                _add(_normalize(word), f"{desc_prefix} {word}".strip())
        else:
            _add(_normalize(sub), sub.strip())

    return tuple(facets[:MAX_FACETS])


def merge_search_results(
    *groups: Sequence["SearchResult"],
) -> list["SearchResult"]:
    """按 chunk_id 稳定去重合并多组检索结果(单调并集)。

    先出现的组优先: 首轮命中的角色/相似度不被深搜重复命中覆盖,
    保证"补搜只增不减"的证据单调性。
    """
    merged: list["SearchResult"] = []
    seen: set[int] = set()
    for group in groups:
        for result in group or []:
            chunk_id = getattr(result, "chunk_id", None)
            if chunk_id is None or chunk_id in seen:
                continue
            seen.add(chunk_id)
            merged.append(result)
    return merged


def facet_hit_prepared(
    norm_query: str,
    probes: tuple[str, ...],
    sub_queries: set[str],
    contents: list[str],
) -> bool:
    """判定核心：预规范化版本，避免在热路径上重复调用 normalize_text。

 公开契约: bind_probe_index / extend_probe_index 共用同一谓词来源，
 禁止复制两份判定逻辑。
 """
    if norm_query in sub_queries:
        return True
    return any(probe in content for content in contents for probe in probes)


def facet_hit(
    facet: EvidenceFacet,
    sub_queries: set[str],
    contents: list[str],
) -> bool:
    """判定单个分面是否被命中: 子查询精确匹配或内容字面探针命中。委托 facet_hit_prepared。

 公开契约(2C): reviewer_parts.evidence_policy 复用同一判定,
 保证提取侧与审查侧的分面归属口径一致。
 """
    return facet_hit_prepared(
        normalize_text(facet.query),
        _literal_probes(facet.key),
        sub_queries,
        contents,
    )


# 向后兼容别名(模块内部与既有调用方仍使用下划线名)
_facet_hit = facet_hit
normalize_text = _normalize


def evaluate_facet_coverage(
    facets: Sequence[EvidenceFacet],
    results: Sequence["SearchResult"],
) -> FacetCoverage:
    """评估检索结果对分面的覆盖: matched_sub_query 精确命中或内容字面命中。"""
    facet_tuple = tuple(facets)
    if not facet_tuple:
        return FacetCoverage((), (), ())

    contents = [_normalize(getattr(r, "content", "") or "") for r in results]
    sub_queries = {
        _normalize(getattr(r, "matched_sub_query", "") or "") for r in results
    }
    sub_queries.discard("")

    covered: list[str] = []
    missing: list[str] = []
    for facet in facet_tuple:
        if _facet_hit(facet, sub_queries, contents):
            covered.append(facet.key)
        else:
            missing.append(facet.key)
    return FacetCoverage(
        expected_keys=tuple(f.key for f in facet_tuple),
        covered_keys=tuple(covered),
        missing_keys=tuple(missing),
    )


def select_facet_balanced_results(
    results: Sequence["SearchResult"],
    facets: Sequence[EvidenceFacet],
    limit: int,
) -> list["SearchResult"]:
    """分面保底选择: 第一轮每分面至多取一条最佳, 剩余额度按相似度全局补齐。

    防止单一强势分面(文档前部高频材料)在截断阶段挤掉其他分面的唯一证据。
    无分面时退化为纯相似度排序截断。
    """
    pool = list(results)
    if limit <= 0 or not pool:
        return []

    facet_tuple = tuple(facets)
    if not facet_tuple:
        ranked = sorted(pool, key=lambda r: -(r.similarity or 0.0))
        return ranked[:limit]

    buckets: dict[str, list["SearchResult"]] = {f.key: [] for f in facet_tuple}
    for result in pool:
        sub_query = {_normalize(getattr(result, "matched_sub_query", "") or "")}
        sub_query.discard("")
        content = [_normalize(getattr(result, "content", "") or "")]
        for facet in facet_tuple:
            if _facet_hit(facet, sub_query, content):
                buckets[facet.key].append(result)
                break

    for bucket in buckets.values():
        bucket.sort(key=lambda r: -(r.similarity or 0.0))

    selected: list["SearchResult"] = []
    seen_ids: set[int] = set()
    for facet in facet_tuple:
        if len(selected) >= limit:
            break
        for result in buckets[facet.key]:
            if result.chunk_id not in seen_ids:
                selected.append(result)
                seen_ids.add(result.chunk_id)
                break

    remainder = [r for r in pool if r.chunk_id not in seen_ids]
    remainder.sort(key=lambda r: -(r.similarity or 0.0))
    for result in remainder:
        if len(selected) >= limit:
            break
        selected.append(result)
        seen_ids.add(result.chunk_id)
    return selected[:limit]
