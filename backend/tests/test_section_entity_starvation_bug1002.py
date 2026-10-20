""" P1: section/entity 通道与饥饿页有界进包。"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.synthetic_data import synthetic_text

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

import pytest

from app.core.retriever.evidence_pipeline.entity_retriever import (
    extract_entity_terms,
    terms_and_pages_from_identifier_index,
)
from app.core.retriever.evidence_pipeline.starvation_expand import (
    fetch_chunks_on_pages,
    merge_unique_hits,
)
from app.core.retriever.semantic_retriever import SearchResult
from app.models.chunk import Chunk


def test_extract_entity_terms_picks_role_and_id() -> None:
    terms = extract_entity_terms("项目经理须持有注册建造师证书编号：ABC-2024-0099")
    assert "项目经理" in terms
    assert any("ABC-2024-0099" in item or "ABC" in item for item in terms)


def test_identifier_index_terms_and_pages() -> None:
    ref = SimpleNamespace(raw_value=synthetic_text('911100', '00MA00', '00001X'), page="36")
    index = SimpleNamespace(
        company_names={},
        person_names={},
        legal_representatives={},
        phones={},
        credit_codes={synthetic_text('911100', '00MA00', '00001X'): [ref]},
        cert_numbers={},
        bank_accounts={},
        subcontractors={},
    )
    terms, pages = terms_and_pages_from_identifier_index(index)
    assert synthetic_text('911100', '00MA00', '00001X') in terms
    assert 36 in pages


def test_merge_unique_hits_keeps_first() -> None:
    first = SearchResult(1, 1, "a", 1, 0.9, role="primary")
    extra = SearchResult(1, 1, "dup", 1, 0.1, role="starvation")
    other = SearchResult(2, 1, "b", 2, 0.2, role="starvation")
    merged = merge_unique_hits([first], [extra, other])
    assert [item.chunk_id for item in merged] == [1, 2]
    assert merged[0].role == "primary"


def _chunk(chunk_id: int, page: int, content: str) -> Chunk:
    chunk = Chunk(
        document_id=9,
        content=content,
        embedding=None,
        chunk_type="text",
        section_path="业绩",
        chunk_fingerprint=f"fp-{chunk_id}",
        embedding_version="bge-m3-v1",
        page_num=page,
        chunk_index=chunk_id,
        char_count=len(content),
    )
    chunk.id = chunk_id
    return chunk


@pytest.mark.asyncio
async def test_fetch_chunks_on_pages_respects_per_page_cap() -> None:
    rows = [
        _chunk(1, 22, "页22-a"),
        _chunk(2, 22, "页22-b"),
        _chunk(3, 22, "页22-c"),
        _chunk(4, 22, "页22-d"),
        _chunk(5, 23, "页23-a"),
    ]

    class _FakeResult:
        def scalars(self):
            return self

        def all(self):
            return rows

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeResult())
    hits = await fetch_chunks_on_pages(
        db, 9, [22, 23], per_page=2, max_pages=8, role="starvation"
    )
    assert [item.chunk_id for item in hits] == [1, 2, 5]
    assert all(item.role == "starvation" for item in hits)
