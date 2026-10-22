"""证据编排器数据契约 

职责: 定义检索查询、通道计划、通道命中与证据包的数据结构
不做: 执行检索、融合排序、覆盖评估、LLM 调用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.core.retriever.evidence_coverage import EvidenceFacet, FacetCoverage
from app.core.retriever.semantic_retriever import SearchResult


@dataclass(frozen=True)
class RetrievalQuery:
    """单条审查项/串标维度的检索入参。"""

    document_id: int
    description: str
    extra_queries: tuple[str, ...] = ()
    category: object = None
    facets: tuple[EvidenceFacet, ...] = ()
    has_toc_map: bool = False
    is_mandatory: bool = False
    top_k: int = 8
    identifier_terms: tuple[str, ...] = ()
    identifier_pages: tuple[int, ...] = ()
    min_similarity: float = 0.3


@dataclass(frozen=True)
class RetrievalPlan:
    """Query Planner 输出: 启用哪些通道以及各通道查询词。"""

    use_dense: bool = True
    use_lexical: bool = True
    use_section: bool = False
    use_entity: bool = False
    use_table: bool = False
    use_image: bool = False
    use_starvation_pages: bool = False
    dense_queries: tuple[str, ...] = ()
    lexical_terms: tuple[str, ...] = ()
    entity_terms: tuple[str, ...] = ()
    section_terms: tuple[str, ...] = ()


@dataclass
class ChannelHits:
    """单通道召回结果, 顺序即该通道排名。"""

    channel: str
    results: list[SearchResult] = field(default_factory=list)


@dataclass
class EvidencePackage:
    """融合后的可回放证据包。"""

    results: list[SearchResult] = field(default_factory=list)
    image_results: list[SearchResult] = field(default_factory=list)
    coverage: Optional[FacetCoverage] = None
    channels_used: tuple[str, ...] = ()
    starvation: bool = False
    uncovered_pages: tuple[int, ...] = ()

    @property
    def all_results(self) -> list[SearchResult]:
        return list(self.results) + list(self.image_results)

    @property
    def facet_missing(self) -> bool:
        if self.coverage is None:
            return False
        return bool(self.coverage.missing_keys)


def contents_of(results: Sequence[SearchResult]) -> tuple[str, ...]:
    return tuple((r.content or "") for r in results)
