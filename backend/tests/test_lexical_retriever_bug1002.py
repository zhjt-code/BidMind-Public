""": 词项检索纯函数与通道规划 / RRF。"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

import pytest

from app.core.retriever.evidence_coverage import EvidenceFacet
from app.core.retriever.evidence_pipeline.contracts import (
    ChannelHits,
    RetrievalQuery,
)
from app.core.retriever.evidence_pipeline.lexical_retriever import (
    lexical_overlap_score,
    normalize_lexical_terms,
    search_lexical_in_document,
)
from app.core.retriever.evidence_pipeline.query_planner import plan_retrieval_channels
from app.core.retriever.evidence_pipeline.rrf import fuse_channel_rankings
from app.core.retriever.semantic_retriever import SearchResult
from app.models.chunk import Chunk


def test_normalize_lexical_terms_drops_short_and_dupes() -> None:
    assert normalize_lexical_terms([" 合同 ", "证", "合同", "营业执照"]) == [
        "合同",
        "营业执照",
    ]


def test_lexical_overlap_prefers_exact_phrase() -> None:
    terms = ["安全生产许可证", "合同"]
    weak = lexical_overlap_score("目录 .......... 3", terms)
    strong = lexical_overlap_score("附安全生产许可证扫描件", terms)
    assert strong > weak
    assert weak == 0.0


def _chunk(chunk_id: int, content: str) -> Chunk:
    chunk = Chunk(
        document_id=3,
        content=content,
        embedding=None,
        chunk_type="text",
        section_path="资格",
        chunk_fingerprint=f"fp-{chunk_id}",
        embedding_version="bge-m3-v1",
        page_num=1,
        chunk_index=chunk_id,
        char_count=len(content),
    )
    chunk.id = chunk_id
    return chunk


@pytest.mark.asyncio
async def test_lexical_search_ranks_literal_hit_first() -> None:
    license_chunk = _chunk(1, "安全生产许可证编号:(鄂)JZ安许证字[2023]001")
    noise_chunk = _chunk(2, "目录 第一章 投标函")

    class _FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [noise_chunk, license_chunk]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeResult())

    hits = await search_lexical_in_document(
        db, 3, ["安全生产许可证"], top_k=2
    )
    assert [item.chunk_id for item in hits] == [1, 2]
    assert hits[0].role == "lexical"
    assert "安全生产许可证" in (hits[0].matched_keywords or [])


@pytest.mark.asyncio
async def test_lexical_search_failure_returns_empty() -> None:
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("pg_trgm missing")
    hits = await search_lexical_in_document(db, 3, ["合同"], top_k=5)
    assert hits == []


def test_planner_enables_dense_and_lexical_by_default() -> None:
    plan = plan_retrieval_channels(
        RetrievalQuery(document_id=1, description="须提供营业执照和安全生产许可证")
    )
    assert plan.use_dense is True
    assert plan.use_lexical is True
    assert plan.use_section is False
    assert plan.use_entity is False
    assert plan.lexical_terms
    assert plan.dense_queries


def test_planner_opens_section_and_entity_on_signals() -> None:
    plan = plan_retrieval_channels(
        RetrievalQuery(
            document_id=1,
            description="项目经理须提供身份证",
            has_toc_map=True,
            identifier_terms=('<PERSON_A>', "4201"),
            category="STAFF",
        )
    )
    assert plan.use_section is True
    assert plan.use_entity is True
    assert plan.entity_terms == ('<PERSON_A>', "4201")
    assert plan.use_image is True


def test_planner_merges_facet_queries() -> None:
    plan = plan_retrieval_channels(
        RetrievalQuery(
            document_id=1,
            description="须提供资格证书",
            facets=(EvidenceFacet(key="社保", query="社保缴费证明"),),
        )
    )
    assert "社保缴费证明" in plan.lexical_terms
    assert "社保缴费证明" in plan.dense_queries


def _sr(chunk_id: int, similarity: float, role: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=1,
        content=f"chunk-{chunk_id}",
        page_num=1,
        similarity=similarity,
        role=role,
    )


def test_rrf_promotes_item_present_in_two_channels() -> None:
    dense = ChannelHits("dense", [_sr(1, 0.9, "primary"), _sr(2, 0.8, "primary")])
    lexical = ChannelHits("lexical", [_sr(2, 0.1, "lexical"), _sr(3, 0.1, "lexical")])
    fused = fuse_channel_rankings([dense, lexical], limit=3)
    assert fused[0].chunk_id == 2
    assert fused[0].role == "primary"
    assert [item.chunk_id for item in fused] == [2, 1, 3]
