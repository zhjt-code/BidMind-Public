"""BidExtractor 输入预算与过长重试测试。"""

import sys
from pathlib import Path

import httpx
import pytest
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings
from app.core.extractor.bid_extractor import BidExtractor
from app.core.retriever.semantic_retriever import SearchResult


class FakeLLMClient:
    def __init__(self, scripted_results):
        self.scripted_results = list(scripted_results)
        self.calls = []

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        if not self.scripted_results:
            raise RuntimeError("missing scripted result")
        current = self.scripted_results.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


def make_api_status_error(message: str, status_code: int = 400) -> APIStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status_code=status_code, request=request)
    return APIStatusError(message, response=response, body={"error": {"message": message}})


def build_search_result(
    chunk_id: int,
    content: str,
    *,
    page_num: int = 1,
    similarity: float = 0.9,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=1,
        content=content,
        page_num=page_num,
        similarity=similarity,
    )


def make_extractor(llm_client, *, top_k: int = 2, max_retries: int = 2) -> BidExtractor:
    extractor = object.__new__(BidExtractor)
    extractor.llm_client = llm_client
    extractor.max_retries = max_retries
    extractor.top_k = top_k
    extractor.item_timeout = 1.0
    return extractor


def build_success_response(requirement_id: int = 1) -> dict:
    return {
        "requirement_id": requirement_id,
        "content": "已找到有效响应内容",
        "evidence_type": "other",
        "source_pages": [1],
        "found": True,
        "completeness": "partial",
        "missing_elements": [],
    }


def test_build_budgeted_context_respects_budget_and_drops_overflow(monkeypatch):
    monkeypatch.setattr(settings, "BID_EXTRACT_CHUNK_CHAR_LIMIT", 60)

    extractor = make_extractor(FakeLLMClient([]), top_k=2)
    search_results = [
        build_search_result(1, "A" * 200, similarity=0.95),
        build_search_result(2, "B" * 200, similarity=0.90),
        build_search_result(3, "C" * 200, similarity=0.40),
    ]

    context, meta = extractor._build_budgeted_context(
        search_results=search_results,
        image_chunks=[],
        context_budget=170,
    )

    assert meta["used_chars"] <= 170
    assert "【片段1】" in context
    assert "【片段3】" not in context
    assert meta["dropped_sections"] >= 1


@pytest.mark.asyncio
async def test_call_llm_extract_retries_after_input_too_long_400(monkeypatch):
    monkeypatch.setattr(settings, "BID_EXTRACT_CONTEXT_CHAR_BUDGET", 420)
    monkeypatch.setattr(settings, "BID_EXTRACT_CHUNK_CHAR_LIMIT", 220)
    monkeypatch.setattr(settings, "BID_EXTRACT_RETRY_REDUCTION_RATIO", 0.5)

    fake_llm = FakeLLMClient([
        make_api_status_error("Range of input length should be [1, 30720]"),
        build_success_response(11),
    ])
    extractor = make_extractor(fake_llm, top_k=2, max_retries=2)
    search_results = [
        build_search_result(1, "正文证据" * 120, similarity=0.95),
        build_search_result(2, "补充证据" * 120, similarity=0.90),
    ]
    image_chunks = [build_search_result(3, "图片证据摘要" * 80, similarity=0.50)]

    result = await extractor._call_llm_extract(
        req_id=11,
        category=None,
        description="测试要求",
        is_mandatory=True,
        search_results=search_results,
        image_chunks=image_chunks,
    )

    assert result is not None
    assert result.requirement_id == 11
    assert len(fake_llm.calls) == 2
    assert len(fake_llm.calls[1]["user_message"]) < len(fake_llm.calls[0]["user_message"])


@pytest.mark.asyncio
async def test_call_llm_extract_short_context_stays_single_attempt(monkeypatch):
    monkeypatch.setattr(settings, "BID_EXTRACT_CONTEXT_CHAR_BUDGET", 5000)
    monkeypatch.setattr(settings, "BID_EXTRACT_CHUNK_CHAR_LIMIT", 500)
    monkeypatch.setattr(settings, "BID_EXTRACT_RETRY_REDUCTION_RATIO", 0.5)

    fake_llm = FakeLLMClient([build_success_response(21)])
    extractor = make_extractor(fake_llm, top_k=2, max_retries=2)
    search_results = [build_search_result(1, "简短证据内容", similarity=0.95)]

    result = await extractor._call_llm_extract(
        req_id=21,
        category=None,
        description="简短测试要求",
        is_mandatory=False,
        search_results=search_results,
        image_chunks=[],
    )

    assert result is not None
    assert result.requirement_id == 21
    assert len(fake_llm.calls) == 1
    assert "TRUNCATED" not in fake_llm.calls[0]["user_message"]
