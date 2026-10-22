"""检索通道规划 

职责: 根据招标要求/串标维度决定启用哪些召回通道及查询词
不做: 执行检索、融合、覆盖评估、LLM 调用
"""

from __future__ import annotations

from app.core.retriever.evidence_pipeline.contracts import RetrievalPlan, RetrievalQuery
from app.core.retriever.evidence_priors import (
    augment_keywords_with_priors,
    augment_queries_with_priors,
)
from app.core.retriever.query_decomposer import get_query_decomposer

_TABLE_HINTS = ("情况表", "汇总表", "明细表", "清单", "一览表", "统计表", "报价表")
_IMAGE_CATEGORIES = frozenset({
    "QUAL", "STAFF", "PERF",
    "qualification", "staffing", "performance",
})

# 词法槽只接受可字面命中的短实义词。带空格/冒号的是"要求前缀+词"
# 的 dense 子查询, 纯 ASCII 是子面标签, 过长的是整句——都无法用 contains 命中,
# 只喂 dense 通道, 不占稀缺的词法槽(MAX_LEXICAL_TERMS=12)。
_LEXICAL_MAX_TERM_LEN = 12


def _is_lexical_worthy(term: str) -> bool:
    """判定词面是否值得占词法槽: 含中文、无空格冒号、长度 2..12。"""
    if not term:
        return False
    if " " in term or "\t" in term or ":" in term or "：" in term:
        return False
    if not (2 <= len(term) <= _LEXICAL_MAX_TERM_LEN):
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in term)


def _order_lexical_terms(
    facet_terms: list[str],
    prior_keywords: list[str],
) -> list[str]:
    """按信息价值排序词法词: 分面词(含 LLM 关键词/翻译触发词)优先于分词与先验。

    分面词来自上游已结构化语义单元(LLM keywords、依据句式客体、子面触发词),
    信息密度高于从原文机械分词的 prior_keywords, 故排在前面优先占满 12 词帽。
    facet.query 里"要求前缀+词"因含空格被 `_is_lexical_worthy` 天然滤除,
    只有干净短语(如"社保缴费证明")才随分面词进入词法槽。
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for term in [*facet_terms, *prior_keywords]:
        if term in seen or not _is_lexical_worthy(term):
            continue
        seen.add(term)
        ordered.append(term)
    return ordered


def _category_key(category: object) -> str:
    if category is None:
        return ""
    name = getattr(category, "name", None)
    if name:
        return str(name)
    return str(category)


def plan_retrieval_channels(query: RetrievalQuery) -> RetrievalPlan:
    """规划通道. P0 默认 dense+lexical; section/entity 仅在调用方给出信号时打开。"""
    decomposer = get_query_decomposer()
    sub_queries = list(decomposer.decompose(query.description or ""))
    for extra in query.extra_queries:
        if extra and extra not in sub_queries and len(sub_queries) < 20:
            sub_queries.append(extra)
    sub_queries = augment_queries_with_priors(
        sub_queries, query.description or "", query.category
    )
    prior_keywords = augment_keywords_with_priors(
        decomposer.extract_keywords(query.description or ""),
        query.category,
    )
    # 分面词(键 + 够格的 query)进词法槽并优先; facet.query 始终喂 dense。
    # "要求前缀+词"的 query 含空格, 由 _is_lexical_worthy 滤除, 不占词法槽。
    facet_terms: list[str] = []
    for facet in query.facets:
        if facet.key:
            facet_terms.append(facet.key)
        if facet.query:
            facet_terms.append(facet.query)
            if facet.query not in sub_queries and len(sub_queries) < 20:
                sub_queries.append(facet.query)

    lexical_terms = _order_lexical_terms(facet_terms, prior_keywords)

    category_key = _category_key(query.category)
    compact = (query.description or "").replace(" ", "")
    use_table = any(hint in compact for hint in _TABLE_HINTS)
    use_image = category_key in _IMAGE_CATEGORIES or query.is_mandatory
    use_section = bool(query.has_toc_map)
    use_entity = bool(query.identifier_terms)

    return RetrievalPlan(
        use_dense=True,
        use_lexical=bool(lexical_terms or sub_queries),
        use_section=use_section,
        use_entity=use_entity,
        use_table=use_table,
        use_image=use_image,
        use_starvation_pages=False,
        dense_queries=tuple(sub_queries[:20]),
        lexical_terms=tuple(lexical_terms[:20]),
        entity_terms=tuple(query.identifier_terms),
        section_terms=tuple(lexical_terms[:12]),
    )
