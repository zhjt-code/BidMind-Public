""" 防御测试：BidExtractor 主流程接入单调补搜。

覆盖风险点(接线级):
1. 首轮检索零候选不得直接落"[未找到响应]"——必须进入补搜, 深搜命中后照常提取;
2. 首轮部分命中(partial)触发补搜, 第二轮 LLM 必须收到首轮+深搜证据并集;
3. 第二轮未找到时保留首轮已找到结果, 最终响应不得退化为未找到。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.bid_envelope import BidEnvelope
from app.core.extractor import bid_envelope_retrieval as retrieval_mod
from app.core.extractor import bid_response_recovery as recovery_mod
from app.core.extractor.bid_extractor import BidExtractor
from app.core.retriever.context_reassembler import ReassembledContext
from app.core.retriever.semantic_retriever import SearchResult, SemanticRetriever
from app.schemas.bid_response import ExtractedBidResponse

_DESC = "投标人须配备个人防护用品、防护药品，落实安全专项费用"


def _sr(chunk_id: int, content: str, similarity: float = 0.6, role: str | None = None) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=77,
        content=content,
        page_num=chunk_id,
        similarity=similarity,
        role=role,
    )


def _extracted(content: str = "投标人提供了防护用品配置", completeness: str = "full") -> ExtractedBidResponse:
    return ExtractedBidResponse(
        requirement_id=1,
        content=content,
        evidence_type="other",
        source_pages=[1],
        found=True,
        completeness=completeness,
        missing_elements=[],
    )


def _requirement() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        description=_DESC,
        category=None,
        is_mandatory=1,
        sub_requirements=None,
        requirement_kind="normal",
        is_key_review=0,
        score_weight=None,
        scoring_metadata=None,
    )


def _envelope() -> BidEnvelope:
    """整本信封夹具(单文档, 对齐历史 bid_document 行为)。"""
    return BidEnvelope(
        documents=(SimpleNamespace(id=77, bid_part=None, parsed=1),)
    )


@asynccontextmanager
async def _session_factory():
    yield SimpleNamespace()


def _make_extractor() -> BidExtractor:
    extractor = object.__new__(BidExtractor)
    extractor.llm_client = SimpleNamespace()
    extractor.max_retries = 1
    extractor.top_k = 8
    extractor.item_timeout = 1.0
    extractor._project_id = None
    return extractor


def _install_llm(extractor: BidExtractor, scripted: list):
    calls: list[dict] = []

    async def _fake_llm(**kwargs):
        calls.append(kwargs)
        if not scripted:
            raise AssertionError("LLM 调用次数超出脚本预期")
        return scripted.pop(0)

    extractor._call_llm_extract = _fake_llm  # type: ignore[method-assign]
    return calls


def _patch_phase1(monkeypatch, primary_results: list[SearchResult]):
    """让 Phase1 的图片补取/回拼/重排在无 DB 下运转。"""

    async def _fake_images(self, **kwargs):
        return []

    async def _fake_expand(self, document_id, search_results, image_chunks=None, *, is_mandatory=False):
        return ReassembledContext(list(search_results), list(image_chunks or []), [])

    async def _fake_rerank(db, results, query_text, limit=None, min_rerank_size=6):
        return list(results)

    monkeypatch.setattr(
        SemanticRetriever, "fetch_image_chunks_by_pages", _fake_images
    )
    monkeypatch.setattr(
        "app.core.retriever.context_reassembler.ContextReassembler.expand", _fake_expand
    )
    monkeypatch.setattr(retrieval_mod, "rerank_results_by_query", _fake_rerank)


async def test_zero_initial_candidates_reaches_recovery(monkeypatch):
    """零候选必须进入补搜: 深搜命中后照常提取, 不得直接落未找到。"""
    extractor = _make_extractor()

    async def _empty_search(**kwargs):
        return []

    extractor._multi_query_search = _empty_search  # type: ignore[method-assign]

    async def _fake_deep(request, retriever, existing_ids):
        return [_sr(5, "深搜命中 医疗器械与药品一览表", role="deep")]

    monkeypatch.setattr(recovery_mod, "run_deep_evidence_search", _fake_deep)

    llm_calls = _install_llm(extractor, [_extracted(content="深搜后提取到防护药品配置")])

    response = await extractor._extract_single_response_decoupled(
        requirement=_requirement(),
        envelope=_envelope(),
        session_factory=_session_factory,
    )

    assert response is not None
    assert response.content == "深搜后提取到防护药品配置", (
        f"零候选应经补搜提取成功, 实际: {response.content}"
    )
    assert len(llm_calls) == 1, "零候选跳过首轮提取, 仅补搜后调用一次 LLM"


async def test_partial_first_round_second_round_receives_union(monkeypatch):
    """首轮 partial 触发补搜, 第二轮输入必须是首轮+深搜并集。"""
    extractor = _make_extractor()
    first_hit = _sr(1, "个人防护用品一览表 安全帽 工作服", similarity=0.8)

    async def _primary_search(**kwargs):
        return [first_hit]

    extractor._multi_query_search = _primary_search  # type: ignore[method-assign]
    _patch_phase1(monkeypatch, [first_hit])

    async def _fake_deep(request, retriever, existing_ids):
        assert 1 in existing_ids, "深搜必须感知首轮已有 chunk"
        return [_sr(9, "安全专项费用保障 分项安全投入报价表", role="deep")]

    monkeypatch.setattr(recovery_mod, "run_deep_evidence_search", _fake_deep)

    llm_calls = _install_llm(
        extractor,
        [
            _extracted(content="仅找到防护用品部分", completeness="partial"),
            _extracted(content="并集后找到用品与费用"),
        ],
    )

    response = await extractor._extract_single_response_decoupled(
        requirement=_requirement(),
        envelope=_envelope(),
        session_factory=_session_factory,
    )

    assert len(llm_calls) == 2, "partial 必须触发一次补搜"
    second_ids = {r.chunk_id for r in llm_calls[1]["search_results"]} | {
        r.chunk_id for r in llm_calls[1]["image_chunks"]
    }
    assert second_ids == {1, 9}, f"第二轮必须收到首轮+深搜并集, 实际: {second_ids}"
    assert response.content == "并集后找到用品与费用"


async def test_second_round_not_found_keeps_first_round_success(monkeypatch):
    """第二轮未找到时保留首轮成功结果, 不得覆盖为未找到。"""
    extractor = _make_extractor()
    first_hit = _sr(1, "个人防护用品一览表", similarity=0.8)

    async def _primary_search(**kwargs):
        return [first_hit]

    extractor._multi_query_search = _primary_search  # type: ignore[method-assign]
    _patch_phase1(monkeypatch, [first_hit])

    async def _fake_deep(request, retriever, existing_ids):
        return [_sr(9, "深搜新增内容", role="deep")]

    monkeypatch.setattr(recovery_mod, "run_deep_evidence_search", _fake_deep)

    llm_calls = _install_llm(
        extractor,
        [
            _extracted(content="首轮已找到防护用品配置", completeness="partial"),
            None,  # 第二轮未找到
        ],
    )

    response = await extractor._extract_single_response_decoupled(
        requirement=_requirement(),
        envelope=_envelope(),
        session_factory=_session_factory,
    )

    assert len(llm_calls) == 2
    assert response.content == "首轮已找到防护用品配置", (
        f"第二轮失败不得抹掉首轮成功, 实际: {response.content}"
    )
