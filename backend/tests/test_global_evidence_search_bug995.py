""" 防御测试: 全局候选排序必须发生在截断之前。

已证实缺陷: fetch_image_chunks_globally / fetch_keyword_chunks_globally
先按页码序 limit 60 再 rerank, 长文档后置页的真实证据根本进不了候选池。
本文件锁定新契约:
1. query 提供时按 PGVector 向量相似度排序后再限额(limit*4), 后置页证据可入选
2. 编码失败降级为首尾轮转采样, 禁止退回"全文页码前N"
3. query 为空时保持页码序 limit 的旧行为
4. 两个全局搜索方法委托后仍保留 chunk_type/section_path 过滤特征
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

import numpy as np
import pytest

from app.core.parser.image_extractor import IMAGE_EVIDENCE_MARKER
from app.core.retriever.global_evidence_search import rank_global_candidates
from app.core.retriever.semantic_retriever import SemanticRetriever
from app.models.chunk import Chunk

FAKE_EMBEDDING = np.array([[0.1] * 1024], dtype=np.float32)


class _FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalarResult(self._items)


def _make_chunk(
    *,
    chunk_id: int,
    content: str,
    page_num: int = 1,
    chunk_index: int = 0,
    chunk_type: str = "image_evidence",
) -> Chunk:
    chunk = Chunk(
        document_id=9,
        content=content,
        embedding=[0.0] * 1024,
        chunk_type=chunk_type,
        section_path="资格要求",
        chunk_fingerprint=f"fp-{chunk_id}",
        embedding_version="bge-m3-v1",
        page_num=page_num,
        chunk_index=chunk_index,
        char_count=len(content),
    )
    chunk.id = chunk_id
    return chunk


@pytest.mark.asyncio
async def test_late_page_high_similarity_evidence_enters_pool(monkeypatch) -> None:
    """后置页高相似证据必须入选: 排序在 SQL 层先于限额发生。

    旧实现 ORDER BY page_num LIMIT 60 会让第61+条(后置页)的真实证据
    永远进不了候选池。新契约: ORDER BY 向量距离, LIMIT limit*4。
    """
    monkeypatch.setattr(
        "app.core.vectorizer.encode_gate.safe_encode",
        AsyncMock(return_value=FAKE_EMBEDDING),
    )
    late = _make_chunk(
        chunk_id=61, page_num=120, chunk_index=610,
        content="后置页真实业绩合同扫描件",
    )
    early = [
        _make_chunk(chunk_id=i, page_num=i, chunk_index=i, content=f"前部无关扫描件{i}")
        for i in range(1, 4)
    ]
    captured: list = []

    async def _execute(stmt):
        captured.append(stmt)
        # 模拟 DB 按向量相似度降序返回: 后置页证据排第一
        return _FakeExecuteResult([late] + early)

    chunks = await rank_global_candidates(
        SimpleNamespace(execute=_execute),
        [
            Chunk.document_id == 9,
            Chunk.chunk_type.in_(("image_annotation", "image_evidence")),
        ],
        "近3年业绩合同",
        15,
    )

    assert len(captured) == 1
    sql = str(captured[0])
    assert "<=>" in sql, "必须按 PGVector 余弦距离排序"
    assert "embedding IS NOT NULL" in sql
    assert "chunks.chunk_type" in sql, "过滤条件必须保留"
    order_clause = sql.split("ORDER BY", 1)[1]
    assert "page_num" not in order_clause, "禁止按页码排序截断"
    params = captured[0].compile().params
    assert 60 in params.values(), "候选池必须为 limit*4=60"

    ids = [c.id for c in chunks]
    assert ids[0] == 61, "后置页高相似证据必须排在候选池首位"


@pytest.mark.asyncio
async def test_encode_failure_falls_back_to_head_tail_sampling(monkeypatch) -> None:
    """编码失败降级: 首尾轮转采样, 前部与后部页面均须有代表。"""
    monkeypatch.setattr(
        "app.core.vectorizer.encode_gate.safe_encode",
        AsyncMock(side_effect=RuntimeError("encode unavailable")),
    )
    head_chunks = [
        _make_chunk(chunk_id=i, page_num=i, chunk_index=i, content=f"前部{i}")
        for i in range(1, 31)
    ]
    tail_chunks = [
        _make_chunk(chunk_id=1000 + i, page_num=120 - i, chunk_index=0, content=f"后部{i}")
        for i in range(30)
    ]
    captured: list = []
    results = [_FakeExecuteResult(head_chunks), _FakeExecuteResult(tail_chunks)]

    async def _execute(stmt):
        captured.append(stmt)
        return results.pop(0)

    chunks = await rank_global_candidates(
        SimpleNamespace(execute=_execute),
        [Chunk.document_id == 9],
        "近3年业绩合同",
        15,
    )

    assert len(captured) == 2, "必须发起头部+尾部两次采样查询"
    assert "DESC" in str(captured[1]), "尾部查询必须按页码降序"
    pages = {c.page_num for c in chunks}
    assert 1 in pages, "前部页面必须有代表"
    assert 120 in pages, "后部页面必须有代表, 禁止退回全文页码前N"
    # 首尾轮转: 第一条来自头部, 第二条来自尾部
    assert chunks[0].id == head_chunks[0].id
    assert chunks[1].id == tail_chunks[0].id
    assert len(chunks) == 60


@pytest.mark.asyncio
async def test_empty_query_keeps_page_order_behavior(monkeypatch) -> None:
    """query 为空时保持旧行为: 页码序 + 原始 limit, 不触发编码。"""
    monkeypatch.setattr(
        "app.core.vectorizer.encode_gate.safe_encode",
        AsyncMock(side_effect=AssertionError("query 为空不得编码")),
    )
    captured: list = []

    async def _execute(stmt):
        captured.append(stmt)
        return _FakeExecuteResult([
            _make_chunk(chunk_id=1, page_num=1, content="第一页"),
        ])

    chunks = await rank_global_candidates(
        SimpleNamespace(execute=_execute),
        [Chunk.document_id == 9],
        None,
        15,
    )

    assert len(captured) == 1
    sql = str(captured[0])
    assert "<=>" not in sql
    order_clause = sql.split("ORDER BY", 1)[1]
    assert "page_num" in order_clause
    assert "chunk_index" in order_clause
    params = captured[0].compile().params
    assert 15 in params.values(), "query 为空时限额保持原始 limit"
    assert 60 not in params.values()
    assert [c.id for c in chunks] == [1]


@pytest.mark.asyncio
async def test_fetch_image_chunks_globally_with_query_uses_vector_ranking(
    monkeypatch,
) -> None:
    """图片全局搜索提供 query 时委托向量排序, 且保留类型/证据标记过滤。"""
    monkeypatch.setattr(
        "app.core.vectorizer.encode_gate.safe_encode",
        AsyncMock(return_value=FAKE_EMBEDDING),
    )
    captured: list = []

    async def _execute(stmt):
        captured.append(stmt)
        return _FakeExecuteResult([
            _make_chunk(chunk_id=61, page_num=120, content="后置页营业执照扫描件"),
            _make_chunk(chunk_id=3, page_num=3, content="前部其他图片说明"),
        ])

    retriever = SemanticRetriever(SimpleNamespace(execute=_execute))
    retriever._get_document = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(doc_type="bid")
    )

    results = await retriever.fetch_image_chunks_globally(
        document_id=9,
        keywords=["营业执照"],
        query="提供营业执照副本并加盖公章",
    )

    sql = str(captured[0])
    assert "<=>" in sql, "query 路径必须走向量排序"
    assert "chunks.chunk_type" in sql
    params = captured[0].compile().params
    assert IMAGE_EVIDENCE_MARKER in params.values()
    assert [r.chunk_id for r in results] == [61, 3]


@pytest.mark.asyncio
async def test_fetch_keyword_chunks_globally_with_query_uses_vector_ranking(
    monkeypatch,
) -> None:
    """结构化全局搜索提供 query 时委托向量排序, 且保留类型/章节路径过滤。"""
    monkeypatch.setattr(
        "app.core.vectorizer.encode_gate.safe_encode",
        AsyncMock(return_value=FAKE_EMBEDDING),
    )
    captured: list = []

    async def _execute(stmt):
        captured.append(stmt)
        return _FakeExecuteResult([
            _make_chunk(
                chunk_id=88, page_num=472, content="正在施工和新承接项目情况表",
                chunk_type="table_row_with_header",
            ),
        ])

    retriever = SemanticRetriever(SimpleNamespace(execute=_execute))
    retriever._get_document = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(doc_type="bid")
    )

    results = await retriever.fetch_keyword_chunks_globally(
        document_id=9,
        keywords=["正在施工", "项目情况表"],
        query="正在施工和新承接的项目情况表",
    )

    sql = str(captured[0])
    assert "<=>" in sql, "query 路径必须走向量排序"
    assert "chunks.chunk_type" in sql
    assert "chunks.section_path" in sql
    params = captured[0].compile().params
    assert "table_row_with_header" in params["chunk_type_1"]
    assert [r.chunk_id for r in results] == [88]
