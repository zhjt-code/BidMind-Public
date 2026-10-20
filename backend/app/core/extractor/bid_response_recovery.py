"""投标响应单调补搜 

职责: 对单条招标要求执行至多一次有界补搜(深搜检索 + 证据并集 + 二轮提取),
 保证补搜后的结果与证据相对首轮单调不减, 并承接未找到时的饥饿标注
不做: 首轮检索、Prompt 模板构造、LLM 客户端实现、ORM 持久化

背景: 旧 _deep_search 分支只在否决项 found=False 等窄条件触发, 且第二轮
LLM 仅消费深搜新增 chunk——第二轮 found=False 会把首轮已找到的事实覆盖成
"[未找到响应]"; 零候选路径则直接早退, 深搜与饥饿检测均不可达。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Sequence, Union

from loguru import logger

from app.core.extractor.extraction_failure import ExtractionFailedSignal
from app.core.retriever.evidence_coverage import (
    EvidenceFacet,
    FacetCoverage,
    merge_search_results,
)
from app.core.retriever.evidence_pipeline.lexical_retriever import (
    search_lexical_in_document,
)
from app.core.retriever.evidence_priors import (
    augment_keywords_with_priors,
    augment_queries_with_priors,
    detect_retrieval_starvation,
    format_starvation_note,
)
from app.core.retriever.query_decomposer import get_query_decomposer
from app.core.retriever.semantic_retriever import SearchResult, SemanticRetriever
from app.schemas.bid_response import ExtractedBidResponse

ExtractionResult = Union[ExtractedBidResponse, ExtractionFailedSignal, None]
# (正文候选, 图片候选) -> 提取结果; 由调用方绑定 Prompt/LLM 细节
ExtractCallback = Callable[
    [list[SearchResult], list[SearchResult]], Awaitable[ExtractionResult]
]

# 深搜子查询上限与单查询召回数(与旧 _deep_search 行为一致)
_DEEP_MAX_QUERIES = 20
_DEEP_PER_QUERY_TOP_K = 5
_DEEP_MIN_SIMILARITY = 0.2


@dataclass(frozen=True)
class BidRecoveryRequest:
    """补搜请求上下文(单条要求维度, 不含可变状态)。"""

    requirement_id: int
    description: str
    category: object
    is_mandatory: bool
    bid_document_id: int
    facets: tuple[EvidenceFacet, ...] = ()
    # 投标信封全部文档 id(分卷=N 个); 为空时回退单文档 bid_document_id
    envelope_document_ids: tuple[int, ...] = ()


def _target_document_ids(request: BidRecoveryRequest) -> tuple[int, ...]:
    """深搜/饥饿检测的目标文档集合(信封优先, 向后兼容单文档)。"""
    return request.envelope_document_ids or (request.bid_document_id,)


@dataclass
class RecoveryOutcome:
    """补搜产出: extracted 为择优结果, evidence 为首轮+深搜稳定并集。"""

    extracted: ExtractionResult
    evidence: list[SearchResult]
    attempted: bool


def _is_found(result: ExtractionResult) -> bool:
    return isinstance(result, ExtractedBidResponse) and bool(result.found)


def should_attempt_recovery(
    initial_extracted: ExtractionResult,
    initial_evidence: Sequence[SearchResult],
    coverage: Optional[FacetCoverage],
) -> bool:
    """判定是否需要一次补搜。

    触发条件(任一): 零候选 / 未找到(含技术失败) / completeness 为
    partial|form_only / missing_elements 非空 / 分面覆盖存在缺口。
    不再以 is_mandatory 为前置——非否决可审项同样允许一次补救。
    """
    if not initial_evidence:
        return True
    if not _is_found(initial_extracted):
        return True
    if (initial_extracted.completeness or "") in ("partial", "form_only"):
        return True
    if initial_extracted.missing_elements:
        return True
    if coverage is not None and coverage.missing_keys:
        return True
    return False


def _choose_better(
    first: ExtractionResult, second: ExtractionResult
) -> ExtractionResult:
    """结果择优: found 有效结果 > 技术失败信号 > 业务未找到(None)。

    第二轮输入是证据并集, found 时信息更全故优先; 第二轮失败/未找到时
    保留首轮成功结果, 技术失败信号不得退化为业务未找到。
    """
    if _is_found(second):
        return second
    if _is_found(first):
        return first
    if isinstance(first, ExtractionFailedSignal):
        return first
    if isinstance(second, ExtractionFailedSignal):
        return second
    return first if first is not None else second


@dataclass(frozen=True)
class _DeepPlan:
    """单条要求的深搜查询计划(跨信封文档复用, 只构建一次)。"""

    description: str
    sub_queries: tuple[str, ...]
    keywords: tuple[str, ...]
    lexical_terms: tuple[str, ...]


def _build_deep_plan(request: BidRecoveryRequest) -> _DeepPlan:
    """构建深搜查询计划: 子查询分解 + 先验增强 + 分面查询 + 词法词表。"""
    decomposer = get_query_decomposer()
    sub_queries = decomposer.decompose(request.description)
    sub_queries = augment_queries_with_priors(
        sub_queries, request.description, request.category
    )
    for facet in request.facets:
        if facet.query not in sub_queries and len(sub_queries) < _DEEP_MAX_QUERIES:
            sub_queries.append(facet.query)
    keywords = augment_keywords_with_priors(
        decomposer.extract_keywords(request.description), request.category
    )
    lexical_terms = tuple(dict.fromkeys([*keywords, *sub_queries[:8]]))
    return _DeepPlan(
        description=request.description,
        sub_queries=tuple(sub_queries[:_DEEP_MAX_QUERIES]),
        keywords=tuple(keywords),
        lexical_terms=lexical_terms,
    )


async def _deep_search_one_document(
    retriever: SemanticRetriever,
    document_id: int,
    plan: _DeepPlan,
    existing_ids: set[int],
    seen: dict[int, SearchResult],
) -> None:
    """在单份文档内执行三通道深搜(语义/词法/图片), 命中写入 seen。"""
    keywords = list(plan.keywords)
    for sub_query in plan.sub_queries:
        results = await retriever.search_in_document(
            document_id=document_id,
            query=sub_query,
            top_k=_DEEP_PER_QUERY_TOP_K,
            min_similarity=_DEEP_MIN_SIMILARITY,
        )
        for result in results:
            if result.chunk_id not in existing_ids and result.chunk_id not in seen:
                result.role = "deep"
                result.matched_sub_query = sub_query
                result.matched_keywords = keywords if keywords else None
                seen[result.chunk_id] = result

    if plan.lexical_terms:
        lexical_hits = await search_lexical_in_document(
            retriever.db,
            document_id,
            list(plan.lexical_terms),
            top_k=max(_DEEP_PER_QUERY_TOP_K * 2, 8),
        )
        for result in lexical_hits:
            if result.chunk_id not in existing_ids and result.chunk_id not in seen:
                result.role = "deep"
                result.matched_keywords = keywords if keywords else None
                seen[result.chunk_id] = result

    if keywords:
        image_results = await retriever.fetch_image_chunks_globally(
            document_id=document_id,
            keywords=keywords,
            query=plan.description,
        )
        for result in image_results:
            if result.chunk_id not in existing_ids and result.chunk_id not in seen:
                result.role = "deep"
                result.matched_keywords = keywords
                seen[result.chunk_id] = result


async def run_deep_evidence_search(
    request: BidRecoveryRequest,
    retriever: SemanticRetriever,
    existing_ids: set[int],
) -> list[SearchResult]:
    """二次深搜: 分面/子查询低阈值检索 + 全文档图片与结构化证据搜索。

 迁移自 BidExtractor._deep_search, 行为保留(先验增强 + query
 相关性截断); 追加分面查询作为深搜通道, 命中统一标记 role=deep。
 投标信封分卷时按信封内全部文档逐份深搜后合并(计划只构建一次)。
 """
    plan = _build_deep_plan(request)
    seen: dict[int, SearchResult] = {}
    for document_id in _target_document_ids(request):
        await _deep_search_one_document(
            retriever, document_id, plan, existing_ids, seen
        )

    deep = sorted(seen.values(), key=lambda r: r.similarity, reverse=True)
    if deep:
        logger.info(
            f"要求 {request.requirement_id} 二次深搜: 新增{len(deep)}个chunk"
        )
    return deep


def build_second_round_callback(
    llm_extract,
    request: BidRecoveryRequest,
    scoring_context: str,
    verified_materials: str = "",
) -> ExtractCallback:
    """构造二轮提取回调: 输入按分面保底排序后交给调用方的 LLM 提取函数。

    防止强势分面(文档前部高频材料)在预算截断阶段挤掉弱势分面的唯一证据。
    """
    from app.core.retriever.evidence_coverage import select_facet_balanced_results

    async def _second_round(
        primary_results: list[SearchResult],
        image_results: list[SearchResult],
    ) -> ExtractionResult:
        balanced = select_facet_balanced_results(
            primary_results, request.facets, limit=len(primary_results)
        )
        return await llm_extract(
            req_id=request.requirement_id,
            category=request.category,
            description=request.description,
            is_mandatory=request.is_mandatory,
            search_results=balanced,
            image_chunks=image_results,
            scoring_context=scoring_context,
            verified_materials=verified_materials,
        )

    return _second_round


async def recover_bid_response(
    request: BidRecoveryRequest,
    initial_extracted: ExtractionResult,
    initial_evidence: Sequence[SearchResult],
    session_factory,
    extract_callback: ExtractCallback,
) -> RecoveryOutcome:
    """执行一次补搜闭环: 深搜 -> 证据并集 -> 二轮提取 -> 结果择优。

    约束: 深搜在短 session 内完成, LLM 回调期间不持有 DB 连接;
    无新增证据时跳过二轮 LLM, 直接保留首轮结果(证据单调不减)。
    """
    existing_ids = {r.chunk_id for r in initial_evidence}
    async with session_factory() as session:
        retriever = SemanticRetriever(session)
        deep_results = await run_deep_evidence_search(request, retriever, existing_ids)

    if not deep_results:
        return RecoveryOutcome(
            extracted=initial_extracted,
            evidence=list(initial_evidence),
            attempted=True,
        )

    merged = merge_search_results(initial_evidence, deep_results)
    primary = [r for r in merged if not SemanticRetriever.is_image_related_result(r)]
    images = [r for r in merged if SemanticRetriever.is_image_related_result(r)]

    second_extracted = await extract_callback(primary, images)
    chosen = _choose_better(initial_extracted, second_extracted)
    if _is_found(initial_extracted) and not _is_found(second_extracted):
        logger.info(
            f"要求 {request.requirement_id}: 二轮提取未优于首轮, 保留首轮结果(证据单调)"
        )
    return RecoveryOutcome(extracted=chosen, evidence=merged, attempted=True)


async def detect_starvation_for_request(
    request: BidRecoveryRequest,
    seen_results: Sequence[SearchResult],
    session_factory,
):
    """对全部可审项做检索饥饿检测; 失败返回 None, 不抛穿主流程。

    信封分卷时逐文档检测, 任一文档饥饿即返回该发现;
    covered_pages 按命中所属文档过滤(命中无 document_id 时保守计入)。
    """
    keywords = get_query_decomposer().extract_keywords(request.description)
    try:
        async with session_factory() as session:
            for document_id in _target_document_ids(request):
                covered_pages = {
                    item.page_num
                    for item in seen_results
                    if item.page_num
                    and getattr(item, "document_id", None) in (None, document_id)
                }
                finding = await detect_retrieval_starvation(
                    session,
                    document_id,
                    request.category,
                    covered_pages=covered_pages,
                    requirement_keywords=keywords,
                )
                if finding is not None:
                    return finding
    except Exception as gate_err:
        logger.warning(
            f"要求 {request.requirement_id}: 检索饥饿检测失败(忽略): {gate_err}"
        )
        return None
    return None


async def annotate_not_found_with_starvation(
    response,
    request: BidRecoveryRequest,
    seen_results: Sequence[SearchResult],
    session_factory,
) -> None:
    """落库前写入饥饿 note; 不再限否决项, 缺口走证据不足标记由调用方决定 content。"""
    finding = await detect_starvation_for_request(
        request, seen_results, session_factory
    )
    if finding:
        response.relevance_note = format_starvation_note(finding)
        logger.warning(
            f"要求 {request.requirement_id}: 检索饥饿 - {response.relevance_note}"
        )
