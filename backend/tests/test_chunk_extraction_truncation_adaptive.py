""": 招标分块提取截断自适应扩容与有损修复防固化。"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)

from app.core.config import settings
from app.core.contracts.degradation_signal import DegradationSignal, push_signal
from app.core.extractor.tender_extractor import TenderExtractor
from app.core.extractor.tender_extractor_parts import chunk_extraction_cache
from app.models.chunk import Chunk


class FakeLLMClient:
    """脚本化 LLM 客户端，记录每次 chat_json 的 max_tokens。"""

    model_name = "fake-model"

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
        if isinstance(current, tuple) and current[0] == "lossy":
            push_signal(DegradationSignal(
                source="json_repair_truncated",
                flow="all",
                repair_type=current[1],
                original_length=2000,
                recovered_elements=len(current[2]),
            ))
            return current[2]
        return current


def build_chunk() -> Chunk:
    return Chunk(
        id=760224,
        document_id=100,
        content="本页包含投标人资格、业绩和人员配置要求。",
        page_num=50,
        chunk_index=0,
        char_count=24,
        chunk_fingerprint="chunk-fingerprint-760224",
    )


def build_raw_requirement(description: str = "投标人须具有有效营业执照") -> dict:
    return {
        "category": "QUAL",
        "description": description,
        "is_mandatory": True,
        "keywords": ["营业执照"],
    }


@pytest.fixture(autouse=True)
def configure_chunk_extract(monkeypatch):
    monkeypatch.setattr(settings, "TENDER_CHUNK_EXTRACT_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "TENDER_EXTRACT_SECTION_CONTEXT_ENABLED", False)

    async def fake_load_cache(*args, **kwargs):
        return None

    monkeypatch.setattr(
        chunk_extraction_cache,
        "load_cached_chunk_requirements",
        fake_load_cache,
    )


@pytest.mark.asyncio
async def test_chunk_extraction_expands_tokens_after_truncated_json_error(monkeypatch):
    truncated = json.JSONDecodeError(
        "Unterminated string starting at",
        '[{"description": "投标人须',
        18,
    )
    fake_llm = FakeLLMClient([truncated, [build_raw_requirement()]])
    extractor = TenderExtractor(db=None, llm_client=fake_llm, max_retries=2)

    saved = []

    async def fake_save_cache(db, key, items, *, empty_result):
        saved.append((items, empty_result))

    monkeypatch.setattr(
        chunk_extraction_cache,
        "save_chunk_requirements_cache",
        fake_save_cache,
    )

    result = await extractor._extract_from_chunk(build_chunk())

    assert result is not None
    assert len(result) == 1
    assert [call["max_tokens"] for call in fake_llm.calls] == [2000, 4000]
    assert len(saved) == 1


@pytest.mark.asyncio
async def test_chunk_extraction_does_not_expand_tokens_for_empty_response(monkeypatch):
    empty = json.JSONDecodeError("Expecting value", "", 0)
    fake_llm = FakeLLMClient([empty, [build_raw_requirement()]])
    extractor = TenderExtractor(db=None, llm_client=fake_llm, max_retries=2)

    async def fake_save_cache(*args, **kwargs):
        return None

    monkeypatch.setattr(
        chunk_extraction_cache,
        "save_chunk_requirements_cache",
        fake_save_cache,
    )

    result = await extractor._extract_from_chunk(build_chunk())

    assert result is not None
    assert [call["max_tokens"] for call in fake_llm.calls] == [2000, 2000]


@pytest.mark.asyncio
async def test_chunk_extraction_empty_llm_error_retries_without_format_hint(
    monkeypatch,
):
    """: EmptyLLMResponseError 不扩容、不追加格式提示，二次调用可成功。"""
    from app.core.ai.llm_errors import EmptyLLMResponseError

    empty = EmptyLLMResponseError(
        "empty",
        model_name="fake-model",
        finish_reason="stop",
        raw_len=0,
    )
    fake_llm = FakeLLMClient([empty, [build_raw_requirement()]])
    extractor = TenderExtractor(db=None, llm_client=fake_llm, max_retries=2)

    async def fake_save_cache(*args, **kwargs):
        return None

    monkeypatch.setattr(
        chunk_extraction_cache,
        "save_chunk_requirements_cache",
        fake_save_cache,
    )

    result = await extractor._extract_from_chunk(build_chunk())

    assert result is not None
    assert len(result) == 1
    assert [call["max_tokens"] for call in fake_llm.calls] == [2000, 2000]
    # 第二次调用的 user_message 不应被追加「请确保输出为有效的 JSON」
    second_user = fake_llm.calls[1]["user_message"]
    assert "请确保输出为有效的 JSON" not in second_user


@pytest.mark.asyncio
async def test_chunk_extraction_empty_llm_error_exhausted_returns_none(monkeypatch):
    from app.core.ai.llm_errors import EmptyLLMResponseError

    empty = EmptyLLMResponseError("empty", raw_len=0)
    fake_llm = FakeLLMClient([empty, empty])
    extractor = TenderExtractor(db=None, llm_client=fake_llm, max_retries=2)

    async def fake_save_cache(*args, **kwargs):
        return None

    monkeypatch.setattr(
        chunk_extraction_cache,
        "save_chunk_requirements_cache",
        fake_save_cache,
    )

    result = await extractor._extract_from_chunk(build_chunk())

    assert result is None
    assert any(
        sf["stage"] == "chunk_extraction" and sf.get("chunk_id") == 760224
        for sf in extractor._stage_failures
    )


@pytest.mark.asyncio
async def test_lossy_repair_retries_with_larger_budget_before_caching(monkeypatch):
    partial = [build_raw_requirement("投标人须具有有效营业执照")]
    complete = [
        build_raw_requirement("投标人须具有有效营业执照"),
        build_raw_requirement("投标人须具有安全生产许可证"),
    ]
    fake_llm = FakeLLMClient([
        ("lossy", "truncated_array", partial),
        complete,
    ])
    extractor = TenderExtractor(db=None, llm_client=fake_llm, max_retries=2)

    saved = []

    async def fake_save_cache(db, key, items, *, empty_result):
        saved.append((items, empty_result))

    monkeypatch.setattr(
        chunk_extraction_cache,
        "save_chunk_requirements_cache",
        fake_save_cache,
    )

    result = await extractor._extract_from_chunk(build_chunk())

    assert result is not None
    assert len(result) == 2
    assert [call["max_tokens"] for call in fake_llm.calls] == [2000, 4000]
    assert len(saved) == 1
    assert len(saved[0][0]) == 2
    assert saved[0][1] is False


@pytest.mark.asyncio
async def test_lossy_repair_keeps_best_partial_when_retry_returns_empty(monkeypatch):
    partial = [build_raw_requirement("投标人须具有有效营业执照")]
    fake_llm = FakeLLMClient([
        ("lossy", "truncated_array", partial),
        [],
    ])
    extractor = TenderExtractor(db=None, llm_client=fake_llm, max_retries=2)

    saved = []

    async def fake_save_cache(db, key, items, *, empty_result):
        saved.append((items, empty_result))

    monkeypatch.setattr(
        chunk_extraction_cache,
        "save_chunk_requirements_cache",
        fake_save_cache,
    )

    result = await extractor._extract_from_chunk(build_chunk())

    assert result is not None
    assert len(result) == 1
    assert [call["max_tokens"] for call in fake_llm.calls] == [2000, 4000]
    assert saved == []
    assert any(
        failure["stage"] == "chunk_partial_recovery"
        for failure in extractor._stage_failures
    )


@pytest.mark.asyncio
async def test_lossy_repair_at_ceiling_returns_partial_without_cache(monkeypatch):
    partial = [build_raw_requirement("投标人须具有有效营业执照")]
    fake_llm = FakeLLMClient([("lossy", "truncated_array", partial)])
    extractor = TenderExtractor(db=None, llm_client=fake_llm, max_retries=1)

    saved = []

    async def fake_save_cache(db, key, items, *, empty_result):
        saved.append((items, empty_result))

    monkeypatch.setattr(
        chunk_extraction_cache,
        "save_chunk_requirements_cache",
        fake_save_cache,
    )

    result = await extractor._extract_from_chunk(
        build_chunk(),
        retry_mode="validation_rescue",
    )

    assert result is not None
    assert len(result) == 1
    assert [call["max_tokens"] for call in fake_llm.calls] == [4000]
    assert saved == []
    assert any(
        failure["stage"] == "chunk_partial_recovery"
        for failure in extractor._stage_failures
    )


@pytest.mark.asyncio
async def test_retry_exhaustion_returns_best_partial_without_cache(monkeypatch):
    partial = [build_raw_requirement("投标人须具有有效营业执照")]
    fake_llm = FakeLLMClient([
        ("lossy", "truncated_array", partial),
        json.JSONDecodeError("Unterminated string starting at", "[{\"x\": \"", 8),
    ])
    extractor = TenderExtractor(db=None, llm_client=fake_llm, max_retries=2)

    saved = []

    async def fake_save_cache(db, key, items, *, empty_result):
        saved.append((items, empty_result))

    monkeypatch.setattr(
        chunk_extraction_cache,
        "save_chunk_requirements_cache",
        fake_save_cache,
    )

    result = await extractor._extract_from_chunk(build_chunk())

    assert result is not None
    assert len(result) == 1
    assert [call["max_tokens"] for call in fake_llm.calls] == [2000, 4000]
    assert saved == []
