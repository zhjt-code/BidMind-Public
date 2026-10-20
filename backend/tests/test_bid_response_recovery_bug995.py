""" 防御测试：单调补搜模块（触发判定 + 证据并集 + 结果不降级）。

覆盖风险点:
1. 零候选必须进入补搜（修复前直接落"[未找到响应]"，深搜不可达）;
2. completeness=partial / missing_elements 非空 / 分面缺口必须触发补搜（修复前不消费）;
3. 第二轮 LLM 必须收到首轮+深搜证据并集（修复前只喂深搜新增，可覆盖首轮成功）;
4. 第二轮失败不得抹掉首轮已找到结果；技术失败信号不得退化为业务未找到;
5. 补搜最多一轮（extract_callback 至多调用一次）。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.extractor import bid_response_recovery as recovery_mod
from app.core.extractor.bid_response_recovery import (
    BidRecoveryRequest,
    recover_bid_response,
    should_attempt_recovery,
)
from app.core.extractor.extraction_failure import ExtractionFailedSignal
from app.core.retriever.evidence_coverage import (
    EvidenceFacet,
    FacetCoverage,
)
from app.core.retriever.semantic_retriever import SearchResult
from app.schemas.bid_response import ExtractedBidResponse


def _sr(
    chunk_id: int,
    content: str,
    role: str = "primary",
    similarity: float = 0.5,
    document_id: int = 1,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        content=content,
        page_num=1,
        similarity=similarity,
        role=role,
    )


def _extracted(
    *,
    found: bool = True,
    completeness: str = "full",
    missing: list[str] | None = None,
    content: str = "投标人提供了对应响应内容",
) -> ExtractedBidResponse:
    return ExtractedBidResponse(
        requirement_id=1,
        content=content,
        evidence_type="other",
        source_pages=[1],
        found=found,
        completeness=completeness,
        missing_elements=missing or [],
    )


def _request(is_mandatory: bool = False) -> BidRecoveryRequest:
    return BidRecoveryRequest(
        requirement_id=1,
        description="投标人须配备个人防护用品、防护药品，落实安全专项费用",
        category=None,
        is_mandatory=is_mandatory,
        bid_document_id=77,
    )


def _no_gap() -> FacetCoverage:
    return FacetCoverage(expected_keys=("a",), covered_keys=("a",), missing_keys=())


def _with_gap() -> FacetCoverage:
    return FacetCoverage(
        expected_keys=("个人防护用品", "防护药品", "安全专项费用"),
        covered_keys=("个人防护用品",),
        missing_keys=("防护药品", "安全专项费用"),
    )


@asynccontextmanager
async def _fake_session_factory():
    yield object()


class TestShouldAttemptRecovery:
    def test_zero_candidates_trigger_even_for_optional_requirement(self):
        """零候选：无论是否否决项都必须补搜（修复前直接落未找到）。"""
        assert should_attempt_recovery(None, [], None) is True

    def test_full_response_without_gap_does_not_trigger(self):
        assert (
            should_attempt_recovery(_extracted(completeness="full"), [_sr(1, "证据")], _no_gap())
            is False
        )

    def test_partial_completeness_triggers(self):
        assert (
            should_attempt_recovery(_extracted(completeness="partial"), [_sr(1, "证据")], _no_gap())
            is True
        )

    def test_missing_elements_trigger(self):
        extracted = _extracted(completeness="full", missing=["防护药品清单"])
        assert should_attempt_recovery(extracted, [_sr(1, "证据")], _no_gap()) is True

    def test_facet_gap_triggers(self):
        assert (
            should_attempt_recovery(_extracted(completeness="full"), [_sr(1, "证据")], _with_gap())
            is True
        )

    def test_not_found_triggers(self):
        assert should_attempt_recovery(None, [_sr(1, "证据")], _no_gap()) is True

    def test_extraction_failed_signal_triggers(self):
        assert (
            should_attempt_recovery(ExtractionFailedSignal(reason="timeout"), [_sr(1, "证据")], _no_gap())
            is True
        )


class TestRecoverBidResponse:
    async def test_second_round_receives_union_of_initial_and_deep(self, monkeypatch):
        initial = [_sr(1, "首轮 个人防护用品表", role="primary"), _sr(2, "首轮 目录", role="toc")]
        deep = [_sr(3, "深搜 防护药品清单", role="deep"), _sr(1, "重复chunk", role="deep")]
        seen_inputs: dict = {}

        async def _fake_deep(request, retriever, existing_ids):
            return [r for r in deep if r.chunk_id not in existing_ids]

        monkeypatch.setattr(recovery_mod, "run_deep_evidence_search", _fake_deep)

        async def _callback(search_results, image_chunks):
            seen_inputs["ids"] = [r.chunk_id for r in search_results] + [
                r.chunk_id for r in image_chunks
            ]
            return _extracted(content="并集提取结果")

        outcome = await recover_bid_response(
            _request(), _extracted(completeness="partial"), initial,
            _fake_session_factory, _callback,
        )
        assert outcome.attempted is True
        assert sorted(seen_inputs["ids"]) == [1, 2, 3], "第二轮必须收到首轮+深搜并集"
        assert sorted(r.chunk_id for r in outcome.evidence) == [1, 2, 3]
        assert outcome.extracted is not None and outcome.extracted.content == "并集提取结果"

    async def test_second_round_not_found_preserves_first_found(self, monkeypatch):
        first = _extracted(completeness="partial", content="首轮已找到")

        async def _fake_deep(request, retriever, existing_ids):
            return [_sr(9, "深搜新增", role="deep")]

        monkeypatch.setattr(recovery_mod, "run_deep_evidence_search", _fake_deep)

        async def _callback(search_results, image_chunks):
            return None  # 第二轮未找到

        outcome = await recover_bid_response(
            _request(), first, [_sr(1, "首轮证据")], _fake_session_factory, _callback,
        )
        assert outcome.extracted is first, "第二轮失败不得抹掉首轮成功结果"
        assert sorted(r.chunk_id for r in outcome.evidence) == [1, 9], "证据仍保留并集"

    async def test_zero_candidates_with_deep_hits_calls_llm_once(self, monkeypatch):
        calls = {"n": 0}

        async def _fake_deep(request, retriever, existing_ids):
            return [_sr(5, "深搜找到的防护药品", role="deep")]

        monkeypatch.setattr(recovery_mod, "run_deep_evidence_search", _fake_deep)

        async def _callback(search_results, image_chunks):
            calls["n"] += 1
            return _extracted(content="深搜后提取成功")

        outcome = await recover_bid_response(
            _request(), None, [], _fake_session_factory, _callback,
        )
        assert calls["n"] == 1, "补搜最多触发一次 LLM"
        assert outcome.extracted is not None and outcome.extracted.found is True

    async def test_no_new_evidence_skips_llm_and_keeps_first(self, monkeypatch):
        first = _extracted(completeness="partial", content="首轮提取结果")
        calls = {"n": 0}

        async def _fake_deep(request, retriever, existing_ids):
            return []

        monkeypatch.setattr(recovery_mod, "run_deep_evidence_search", _fake_deep)

        async def _callback(search_results, image_chunks):
            calls["n"] += 1
            return _extracted(content="不应被调用")

        outcome = await recover_bid_response(
            _request(), first, [_sr(1, "首轮证据")], _fake_session_factory, _callback,
        )
        assert calls["n"] == 0, "无新增证据时不得重复调用 LLM"
        assert outcome.extracted is first

    async def test_technical_failure_not_degraded_to_not_found(self, monkeypatch):
        failure = ExtractionFailedSignal(reason="timeout")

        async def _fake_deep(request, retriever, existing_ids):
            return []

        monkeypatch.setattr(recovery_mod, "run_deep_evidence_search", _fake_deep)

        async def _callback(search_results, image_chunks):
            return None

        outcome = await recover_bid_response(
            _request(), failure, [_sr(1, "首轮证据")], _fake_session_factory, _callback,
        )
        assert isinstance(outcome.extracted, ExtractionFailedSignal), (
            "技术失败必须保留信号，不得退化为业务未找到"
        )

    async def test_second_round_failure_signal_kept_when_first_none(self, monkeypatch):
        async def _fake_deep(request, retriever, existing_ids):
            return [_sr(6, "深搜证据", role="deep")]

        monkeypatch.setattr(recovery_mod, "run_deep_evidence_search", _fake_deep)

        async def _callback(search_results, image_chunks):
            return ExtractionFailedSignal(reason="timeout")

        outcome = await recover_bid_response(
            _request(), None, [], _fake_session_factory, _callback,
        )
        assert isinstance(outcome.extracted, ExtractionFailedSignal)


class _EnvelopeDeepRetriever:
    """信封深搜假检索器: 记录被搜索的文档, 只实现语义通道。"""

    def __init__(self, hits_by_doc: dict[int, list[SearchResult]]):
        self.db = object()
        self.hits_by_doc = hits_by_doc
        self.searched_docs: list[int] = []

    async def search_in_document(self, *, document_id, query, top_k, min_similarity):
        self.searched_docs.append(document_id)
        return list(self.hits_by_doc.get(document_id, []))


class TestRunDeepEvidenceSearchEnvelope:
    """投标信封分卷: 深搜必须覆盖信封内全部文档且跨文档去重。"""

    async def test_envelope_documents_all_searched_and_deduped(self, monkeypatch):
        monkeypatch.setattr(
            recovery_mod,
            "_build_deep_plan",
            lambda request: recovery_mod._DeepPlan(
                description="d", sub_queries=("q1",), keywords=(), lexical_terms=(),
            ),
        )
        retriever = _EnvelopeDeepRetriever({
            11: [
                _sr(101, "商务标命中", document_id=11, similarity=0.4),
                _sr(102, "商务标命中2", document_id=11, similarity=0.9),
            ],
            12: [_sr(201, "技术标命中", document_id=12, similarity=0.6)],
        })
        request = BidRecoveryRequest(
            requirement_id=1,
            description="要求",
            category=None,
            is_mandatory=False,
            bid_document_id=11,
            envelope_document_ids=(11, 12),
        )

        deep = await recovery_mod.run_deep_evidence_search(
            request, retriever, existing_ids={101},
        )

        assert retriever.searched_docs == [11, 12], "信封内每份文档都必须深搜"
        assert [r.chunk_id for r in deep] == [102, 201], "去重后按相似度降序"
        assert all(r.role == "deep" for r in deep)

    async def test_without_envelope_falls_back_to_single_document(self, monkeypatch):
        """向后兼容: envelope_document_ids 为空时仍按单文档深搜。"""
        monkeypatch.setattr(
            recovery_mod,
            "_build_deep_plan",
            lambda request: recovery_mod._DeepPlan(
                description="d", sub_queries=("q1",), keywords=(), lexical_terms=(),
            ),
        )
        retriever = _EnvelopeDeepRetriever({77: [_sr(7, "整本命中", document_id=77)]})

        deep = await recovery_mod.run_deep_evidence_search(
            _request(), retriever, existing_ids=set(),
        )

        assert retriever.searched_docs == [77]
        assert [r.chunk_id for r in deep] == [7]
