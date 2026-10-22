"""投标信封首轮证据检索

职责: 信封级首轮证据检索——逐文档执行单文档多路检索/图片扩展/上下文重组并合并
不做: 不调用 LLM、不执行补搜、不落库、不判定证据充分性

单文档信封(整本)保持与历史 BidExtractor Phase 1 完全相同的调用序列与结果顺序;
多文档信封(分卷)按文档独立检索后合并去重, 图片扩展只在命中所在文档内取页
(形态: 图片语义必须绑定 document_id, 禁止用 A 卷页码去 B 卷取图)。
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from app.core.retriever.context_reassembler import ContextReassembler
from app.core.retriever.evidence_ranker import rerank_results_by_query
from app.core.retriever.semantic_retriever import SearchResult, SemanticRetriever

# 单文档多路检索回调: (retriever, document_id) -> 检索结果
# (要求描述/类别/分面由调用方预先绑定, 本模块不感知检索内部契约)
SearchSingleDocument = Callable[
    [SemanticRetriever, int], Awaitable[list[SearchResult]]
]


@dataclass(frozen=True)
class FirstRoundEvidence:
    """首轮证据包: 正文命中 + 图片命中(均已定序、已补 role)。"""

    primary_results: list[SearchResult]
    image_results: list[SearchResult]


async def retrieve_first_round_evidence(
    retriever: SemanticRetriever,
    reassembler: ContextReassembler,
    envelope_document_ids: Sequence[int],
    *,
    search_single_document: SearchSingleDocument,
    requirement_description: str,
    is_mandatory: bool,
    top_k: int,
) -> FirstRoundEvidence:
    """对信封内每份文档执行首轮检索并合并证据。

    Args:
        retriever: 语义检索器(短 session 生命周期由调用方管理)
        reassembler: 上下文重组器
        envelope_document_ids: 信封内全部文档 id(整本=1 个, 分卷=N 个)
        search_single_document: 单文档多路检索回调
        requirement_description: 招标要求原文(图片证据重排用)
        is_mandatory: 是否否决项(重组预算用)
        top_k: 单文档检索预算(多文档合并后正文截 top_k*2)
    """
    all_primary: list[SearchResult] = []
    all_images: list[SearchResult] = []

    for document_id in envelope_document_ids:
        search_results = await search_single_document(retriever, document_id)
        image_chunks: list[SearchResult] = []
        if search_results:
            page_nums = {r.page_num for r in search_results if r.page_num}
            existing_ids = {r.chunk_id for r in search_results}

            # 形态: 图片扩展只用本文档命中页码, 不跨文档取页
            image_chunks = await retriever.fetch_image_chunks_by_pages(
                document_id=document_id,
                page_nums=page_nums,
                expand_range=2,
                existing_chunk_ids=existing_ids,
            )
            reassembled = await reassembler.expand(
                document_id,
                search_results,
                image_chunks=image_chunks,
                is_mandatory=is_mandatory,
            )
            search_results = reassembled.primary_results
            image_chunks = reassembled.image_results
        all_primary.extend(search_results)
        all_images.extend(image_chunks)

    if len(envelope_document_ids) > 1:
        # ponytail: 多卷共享同一 top_k*2 正文证据预算(全局融合分排序),
        # 弱势卷可能被强势卷挤出; 升级路径是按卷配额(每卷保底 N 条)
        deduped: dict[int, SearchResult] = {}
        for result in all_primary:
            if result.chunk_id not in deduped:
                deduped[result.chunk_id] = result
        all_primary = sorted(
            deduped.values(),
            key=lambda r: r.similarity,
            reverse=True,
        )[: top_k * 2]

    if all_images:
        # 图片证据按与要求的相关性重排+去重, 替代页码序
        all_images = await rerank_results_by_query(
            retriever.db, all_images, requirement_description
        )

    # Fix-02: 未被前序标记过的结果补回 role
    for result in all_primary:
        if not result.role:
            result.role = "primary"
    for result in all_images:
        result.role = "image"

    return FirstRoundEvidence(
        primary_results=all_primary, image_results=all_images
    )
