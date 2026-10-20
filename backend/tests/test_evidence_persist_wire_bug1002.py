""": 落库闸门接入提取主路径, 审查证据不足跳过 LLM。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

from tests.synthetic_data import synthetic_text

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.ai.reviewer import ReviewOrchestrator
from app.core.ai.reviewer_parts.pair_review import review_single_pair
from app.core.bid_envelope import BidEnvelope
from app.core.extractor import bid_envelope_retrieval as retrieval_mod
from app.core.extractor import bid_extractor as bid_extractor_mod
from app.core.extractor.bid_extractor import BidExtractor
from app.core.extractor.evidence_verdict import (
    EVIDENCE_INSUFFICIENT_CONTENT,
    HIT_UNCONFIRMED_NOTE,
    NOT_FOUND_CONTENT,
)
from app.core.matcher.requirement_matcher import MatchedPair
from app.core.retriever.context_reassembler import ReassembledContext
from app.core.retriever.evidence_priors import StarvationFinding
from app.core.retriever.semantic_retriever import SearchResult, SemanticRetriever
from app.schemas.bid_response import ExtractedBidResponse


def _sr(chunk_id: int, content: str, similarity: float = 0.8) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=77,
        content=content,
        page_num=chunk_id,
        similarity=similarity,
        role="primary",
    )


def _extracted_not_found() -> ExtractedBidResponse:
    return ExtractedBidResponse(
        requirement_id=1,
        content="模型未确认到对应响应内容",
        evidence_type="other",
        source_pages=[1],
        found=False,
        completeness="partial",
        missing_elements=[],
    )


def _requirement() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        description="投标人须提供营业执照",
        category=None,
        is_mandatory=1,
        sub_requirements=None,
        requirement_kind="normal",
        is_key_review=0,
        score_weight=None,
        scoring_metadata=None,
    )


@asynccontextmanager
async def _session_factory():
    yield SimpleNamespace()


def _envelope() -> BidEnvelope:
    """整本信封夹具(单文档 id=77, 对齐历史 bid_document 行为)。"""
    return BidEnvelope(
        documents=(SimpleNamespace(id=77, bid_part=None, parsed=1),)
    )


def _make_extractor() -> BidExtractor:
    extractor = object.__new__(BidExtractor)
    extractor.llm_client = SimpleNamespace()
    extractor.max_retries = 1
    extractor.top_k = 8
    extractor.item_timeout = 1.0
    extractor._project_id = None
    return extractor


def _install_llm(extractor: BidExtractor, scripted: list) -> None:
    async def _fake_llm(**kwargs):
        if not scripted:
            raise AssertionError("LLM 调用次数超出脚本预期")
        return scripted.pop(0)

    extractor._call_llm_extract = _fake_llm  # type: ignore[method-assign]


def _patch_phase1(monkeypatch) -> None:
    async def _fake_images(self, **kwargs):
        return []

    async def _fake_expand(self, document_id, search_results, image_chunks=None, *, is_mandatory=False):
        return ReassembledContext(list(search_results), list(image_chunks or []), [])

    async def _fake_rerank(db, results, query_text, limit=None, min_rerank_size=6):
        return list(results)

    monkeypatch.setattr(SemanticRetriever, "fetch_image_chunks_by_pages", _fake_images)
    monkeypatch.setattr(
        "app.core.retriever.context_reassembler.ContextReassembler.expand", _fake_expand
    )
    monkeypatch.setattr(retrieval_mod, "rerank_results_by_query", _fake_rerank)


async def test_starvation_persists_insufficient_not_missing(monkeypatch) -> None:
    extractor = _make_extractor()
    hit = _sr(1, synthetic_text('营业执照扫描件 ', '统一社会信用代码 ', '91110000M', 'A0000001X'))

    async def _primary(**kwargs):
        return [hit]

    extractor._multi_query_search = _primary  # type: ignore[method-assign]
    _patch_phase1(monkeypatch)
    monkeypatch.setattr(bid_extractor_mod, "should_attempt_recovery", lambda *a, **k: False)

    async def _starved(*a, **k):
        return StarvationFinding(
            uncovered_pages=(22, 23, 24),
            candidate_page_count=6,
            phrases_used=("营业执照",),
        )

    monkeypatch.setattr(bid_extractor_mod, "detect_starvation_for_request", _starved)
    _install_llm(extractor, [_extracted_not_found()])
    response = await extractor._extract_single_response_decoupled(
        requirement=_requirement(),
        envelope=_envelope(),
        session_factory=_session_factory,
    )
    assert response.content == EVIDENCE_INSUFFICIENT_CONTENT
    pending = getattr(response, "_pending_retrieval_chunks", [])
    assert pending and pending[0]["chunk_id"] == 1


async def test_covered_pack_persists_partial_hit(monkeypatch) -> None:
    extractor = _make_extractor()
    hit = _sr(2, "附营业执照复印件，住所北京市，成立日期2010年")

    async def _primary(**kwargs):
        return [hit]

    extractor._multi_query_search = _primary  # type: ignore[method-assign]
    _patch_phase1(monkeypatch)
    monkeypatch.setattr(bid_extractor_mod, "should_attempt_recovery", lambda *a, **k: False)

    async def _no_starve(*a, **k):
        return None

    monkeypatch.setattr(bid_extractor_mod, "detect_starvation_for_request", _no_starve)
    monkeypatch.setattr(
        bid_extractor_mod,
        "evaluate_facet_coverage",
        lambda *a, **k: SimpleNamespace(missing_keys=()),
    )
    _install_llm(extractor, [_extracted_not_found()])
    response = await extractor._extract_single_response_decoupled(
        requirement=_requirement(),
        envelope=_envelope(),
        session_factory=_session_factory,
    )
    assert HIT_UNCONFIRMED_NOTE in (response.relevance_note or "")
    assert NOT_FOUND_CONTENT not in (response.content or "")
    assert "检索已命中待确认" in response.content


async def test_low_info_exhausted_pack_allows_not_found(monkeypatch) -> None:
    extractor = _make_extractor()
    hit = _sr(3, "目 录 ........................ 1")

    async def _primary(**kwargs):
        return [hit]

    extractor._multi_query_search = _primary  # type: ignore[method-assign]
    _patch_phase1(monkeypatch)
    monkeypatch.setattr(bid_extractor_mod, "should_attempt_recovery", lambda *a, **k: False)

    async def _no_starve(*a, **k):
        return None

    monkeypatch.setattr(bid_extractor_mod, "detect_starvation_for_request", _no_starve)
    monkeypatch.setattr(
        bid_extractor_mod,
        "evaluate_facet_coverage",
        lambda *a, **k: SimpleNamespace(missing_keys=()),
    )
    _install_llm(extractor, [_extracted_not_found()])
    response = await extractor._extract_single_response_decoupled(
        requirement=_requirement(),
        envelope=_envelope(),
        session_factory=_session_factory,
    )
    assert response.content == NOT_FOUND_CONTENT


async def test_evidence_insufficient_skips_review_llm() -> None:
    pair = MatchedPair(
        requirement=SimpleNamespace(
            id=1,
            description="须提供营业执照",
            category=None,
            is_mandatory=1,
            canonical_key=None,
            main_requirement_id=None,
        ),
        response=SimpleNamespace(
            id=9,
            content=EVIDENCE_INSUFFICIENT_CONTENT,
            document_id=77,
        ),
    )
    captured = SimpleNamespace(
        comment="审查失败占位",
        reconcile_note="",
        consistency_flag="unchecked",
    )
    llm_calls = []

    async def _llm(*args, **kwargs):
        llm_calls.append(1)
        raise AssertionError("证据不足不得调用审查 LLM")

    orch = object.__new__(ReviewOrchestrator)
    orch.item_timeout = 5
    orch._create_failure_result = lambda project_id, pair: captured
    orch._call_llm_review = _llm
    result = await review_single_pair(orch, 1, pair)
    assert llm_calls == []
    assert result.consistency_flag == "needs_review"
    assert "未提供" not in (result.comment or "")
    assert "未找到响应" not in (result.comment or "")
