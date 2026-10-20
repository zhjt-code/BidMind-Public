"""投标信封首轮检索防御测试。

风险点:
1. 单文档(整本)必须保持历史调用序列与结果顺序(零行为变化回归防线);
2. 多文档(分卷)正文并集去重、按融合分排序、截共享预算;
3. 图片扩展禁止跨文档取页(形态);
4. 无命中文档不触发图片扩展与重组。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.extractor import bid_envelope_retrieval as retrieval_mod
from app.core.extractor.bid_envelope_retrieval import retrieve_first_round_evidence
from app.core.retriever.semantic_retriever import SearchResult


def _sr(
    chunk_id: int,
    document_id: int,
    page_num: int = 1,
    similarity: float = 0.5,
    role: str | None = None,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        content=f"chunk-{chunk_id}",
        page_num=page_num,
        similarity=similarity,
        role=role,
    )


class _FakeRetriever:
    """记录图片扩展调用的假检索器(只实现本模块消费的接口)。"""

    def __init__(self, images_by_doc: dict[int, list[SearchResult]] | None = None):
        self.db = object()
        self.images_by_doc = images_by_doc or {}
        self.image_calls: list[dict] = []

    async def fetch_image_chunks_by_pages(
        self, *, document_id, page_nums, expand_range, existing_chunk_ids
    ):
        self.image_calls.append(
            {"document_id": document_id, "page_nums": set(page_nums)}
        )
        return list(self.images_by_doc.get(document_id, []))


class _FakeReassembler:
    """透传重组器: 记录调用文档, 原样返回输入。"""

    def __init__(self):
        self.expand_calls: list[int] = []

    async def expand(self, document_id, search_results, *, image_chunks, is_mandatory):
        self.expand_calls.append(document_id)
        return SimpleNamespace(
            primary_results=search_results, image_results=image_chunks
        )


@pytest.fixture(autouse=True)
def _identity_rerank(monkeypatch):
    """图片重排替换为恒等(重排算法非本模块职责)。"""

    async def _passthrough(session, results, query):
        return list(results)

    monkeypatch.setattr(retrieval_mod, "rerank_results_by_query", _passthrough)


def _search_callback(hits_by_doc: dict[int, list[SearchResult]]):
    calls: list[int] = []

    async def _search(retriever, document_id):
        calls.append(document_id)
        return list(hits_by_doc.get(document_id, []))

    return _search, calls


async def test_single_document_keeps_order_and_sequence():
    """整本: 结果顺序不重排不截断, 与历史 Phase 1 行为一致。"""
    # 故意乱序相似度, 单文档不得被重排
    hits = [_sr(1, 11, page_num=3, similarity=0.2), _sr(2, 11, page_num=5, similarity=0.9)]
    search, calls = _search_callback({11: hits})
    retriever = _FakeRetriever(images_by_doc={11: [_sr(9, 11, page_num=4)]})
    reassembler = _FakeReassembler()

    evidence = await retrieve_first_round_evidence(
        retriever, reassembler, [11],
        search_single_document=search,
        requirement_description="要求",
        is_mandatory=False,
        top_k=1,
    )

    assert calls == [11]
    assert reassembler.expand_calls == [11]
    assert [r.chunk_id for r in evidence.primary_results] == [1, 2]
    assert [r.chunk_id for r in evidence.image_results] == [9]
    assert all(r.role == "primary" for r in evidence.primary_results)
    assert all(r.role == "image" for r in evidence.image_results)


async def test_multi_document_union_dedupe_sort_and_budget():
    """分卷: 并集去重、按相似度降序、截 top_k*2 共享预算。"""
    hits = {
        11: [_sr(1, 11, similarity=0.3), _sr(2, 11, similarity=0.8)],
        12: [_sr(3, 12, similarity=0.6), _sr(2, 12, similarity=0.8), _sr(4, 12, similarity=0.1)],
    }
    search, calls = _search_callback(hits)
    retriever = _FakeRetriever()
    reassembler = _FakeReassembler()

    evidence = await retrieve_first_round_evidence(
        retriever, reassembler, [11, 12],
        search_single_document=search,
        requirement_description="要求",
        is_mandatory=True,
        top_k=1,  # 预算 top_k*2 = 2
    )

    assert calls == [11, 12]
    # chunk 2 去重, 排序后 [2(0.8), 3(0.6), 1(0.3), 4(0.1)] 截 2 条
    assert [r.chunk_id for r in evidence.primary_results] == [2, 3]


async def test_image_expansion_stays_within_hit_document():
    """图片扩展只用命中所在文档的页码(: 禁止跨文档取图)。"""
    hits = {
        11: [_sr(1, 11, page_num=3)],
        12: [_sr(2, 12, page_num=30)],
    }
    search, _ = _search_callback(hits)
    retriever = _FakeRetriever()
    reassembler = _FakeReassembler()

    await retrieve_first_round_evidence(
        retriever, reassembler, [11, 12],
        search_single_document=search,
        requirement_description="要求",
        is_mandatory=False,
        top_k=4,
    )

    assert retriever.image_calls == [
        {"document_id": 11, "page_nums": {3}},
        {"document_id": 12, "page_nums": {30}},
    ]


async def test_document_without_hits_skips_image_and_reassemble():
    hits = {11: [_sr(1, 11, page_num=2)], 12: []}
    search, calls = _search_callback(hits)
    retriever = _FakeRetriever()
    reassembler = _FakeReassembler()

    evidence = await retrieve_first_round_evidence(
        retriever, reassembler, [11, 12],
        search_single_document=search,
        requirement_description="要求",
        is_mandatory=False,
        top_k=4,
    )

    assert calls == [11, 12]
    assert [c["document_id"] for c in retriever.image_calls] == [11]
    assert reassembler.expand_calls == [11]
    assert [r.chunk_id for r in evidence.primary_results] == [1]
