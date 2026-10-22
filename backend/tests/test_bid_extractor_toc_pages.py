"""投标响应提取目录导航页码坐标回归测试。"""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://x:x@localhost/x",
)


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows, document=None):
        self._rows = rows
        self._document = document

    def scalars(self):
        return _FakeScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._document


class _FakeDb:
    def __init__(self, rows, document=None):
        self.rows = rows
        self.document = document
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeExecuteResult(self.rows, self.document)


class _FakeRetriever:
    def __init__(self, doc, rows):
        self._doc = doc
        self.db = _FakeDb(rows, document=doc)

    async def _get_document(self, _document_id: int):
        return self._doc


def _chunk(chunk_id: int, page_num: int, content: str):
    return SimpleNamespace(
        id=chunk_id,
        document_id=77,
        content=content,
        page_num=page_num,
        chunk_type="paragraph",
    )


@pytest.mark.asyncio
async def test_toc_guided_fetch_uses_physical_pages_from_v2_toc_map() -> None:
    """TOC 导航应使用 v2 physical_page，不能继续使用目录 printed_page。"""
    from app.core.extractor.bid_extractor import BidExtractor
    from app.core.parser.toc_parser import serialize_toc_map
    from app.models.document import DocumentType

    toc_map = {
        "version": 2,
        "offset": 2,
        "confidence": "high",
        "enabled": True,
        "entries": [
            {"title": "一、公司简介", "printed_page": 1, "physical_page": 3},
            {"title": "二、项目业绩", "printed_page": 2, "physical_page": 4},
        ],
        "keywords": {
            "公司简介": [{"title": "一、公司简介", "printed_page": 1, "physical_page": 3}],
            "项目业绩": [{"title": "二、项目业绩", "printed_page": 2, "physical_page": 4}],
        },
    }
    doc = SimpleNamespace(doc_type=DocumentType.BID, toc_map=serialize_toc_map(toc_map))
    rows = [_chunk(401, 4, "项目业绩\n近三年完成多个同类项目。")]
    retriever = _FakeRetriever(doc, rows)
    extractor = object.__new__(BidExtractor)

    results = await extractor._toc_guided_fetch(  # noqa: SLF001
        retriever=retriever,
        document_id=77,
        description="投标人需提供项目业绩证明",
        existing_ids=set(),
    )

    assert [result.page_num for result in results] == [4]
    assert [result.role for result in results] == ["toc"]
    params: list = []
    for stmt in retriever.db.executed:
        params.extend(stmt.compile().params.values())
    assert 4 in params or [4] in params
    assert 2 not in params


@pytest.mark.asyncio
async def test_toc_guided_fetch_ignores_legacy_toc_map() -> None:
    """legacy toc_map 坐标系不明，不能用于 chunk 页码查询。"""
    from app.core.extractor.bid_extractor import BidExtractor
    from app.core.parser.toc_parser import serialize_toc_map
    from app.models.document import DocumentType

    doc = SimpleNamespace(
        doc_type=DocumentType.BID,
        toc_map=serialize_toc_map({"项目业绩": [2]}),
    )
    retriever = _FakeRetriever(doc, [_chunk(201, 2, "错误页内容")])
    extractor = object.__new__(BidExtractor)

    results = await extractor._toc_guided_fetch(  # noqa: SLF001
        retriever=retriever,
        document_id=77,
        description="投标人需提供项目业绩证明",
        existing_ids=set(),
    )

    assert results == []
    chunk_queries = [
        stmt for stmt in retriever.db.executed
        if "page_num" in str(stmt.compile())
    ]
    assert chunk_queries == []
