from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.extractor.tender_extractor_parts.performance_metrics import (
    TenderExtractionMetrics,
)
from app.schemas.requirement import ExtractedRequirement
from app.models.tender_chunk_extraction_cache import TenderChunkExtractionCache


def test_tender_chunk_extraction_cache_model_has_unique_reuse_key():
    columns = TenderChunkExtractionCache.__table__.columns

    for column_name in (
        "document_id",
        "chunk_id",
        "chunk_fingerprint",
        "prompt_signature",
        "model_id",
        "extractor_schema_version",
        "section_context_enabled",
        "result_items",
        "empty_result",
        "failure_fingerprint",
    ):
        assert column_name in columns

    unique_constraints = [
        constraint
        for constraint in TenderChunkExtractionCache.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in unique_constraints
    }

    assert (
        "document_id",
        "chunk_fingerprint",
        "prompt_signature",
        "model_id",
        "extractor_schema_version",
        "section_context_enabled",
    ) in unique_column_sets


def test_tender_chunk_extraction_cache_has_default_config_switches():
    assert settings.TENDER_CHUNK_EXTRACT_CACHE_ENABLED is True
    assert settings.TENDER_CHUNK_EXTRACT_CACHE_SCHEMA_VERSION == "v1"


def test_build_chunk_extraction_cache_key_changes_with_prompt_and_model():
    from app.core.extractor.tender_extractor_parts.chunk_extraction_cache import (
        build_chunk_extraction_cache_key,
    )

    chunk = SimpleNamespace(
        id=10,
        document_id=20,
        chunk_fingerprint="abc",
    )

    key_a = build_chunk_extraction_cache_key(
        chunk=chunk,
        system_msg="system",
        user_msg="user-a",
        model_id="model-a",
        schema_version="v1",
        section_context_enabled=True,
    )
    key_b = build_chunk_extraction_cache_key(
        chunk=chunk,
        system_msg="system",
        user_msg="user-b",
        model_id="model-a",
        schema_version="v1",
        section_context_enabled=True,
    )
    key_c = build_chunk_extraction_cache_key(
        chunk=chunk,
        system_msg="system",
        user_msg="user-a",
        model_id="model-b",
        schema_version="v1",
        section_context_enabled=True,
    )

    assert key_a.prompt_signature != key_b.prompt_signature
    assert key_a.model_id != key_c.model_id


@pytest.mark.asyncio
async def test_extract_from_chunk_uses_cache_without_calling_llm(monkeypatch):
    from app.core.extractor.tender_extractor_parts import extraction_stage
    from app.core.extractor.tender_extractor_parts import chunk_extraction_cache

    cached_item = ExtractedRequirement(
        category="QUAL",
        description="投标人须具备有效营业执照",
        is_mandatory=True,
        keywords=["营业执照"],
        source_pages=[3],
    )

    async def fake_load_cached_chunk_requirements(db, key):
        return [cached_item]

    async def fake_save_chunk_requirements_cache(*args, **kwargs):
        raise AssertionError("缓存命中时不应写缓存")

    class FailingLLM:
        async def chat_json(self, **kwargs):
            raise AssertionError("缓存命中时不应调用 LLM")

    monkeypatch.setattr(settings, "TENDER_EXTRACT_SECTION_CONTEXT_ENABLED", False)
    monkeypatch.setattr(settings, "TENDER_CHUNK_EXTRACT_CACHE_ENABLED", True)
    monkeypatch.setattr(
        chunk_extraction_cache,
        "load_cached_chunk_requirements",
        fake_load_cached_chunk_requirements,
    )
    monkeypatch.setattr(
        chunk_extraction_cache,
        "save_chunk_requirements_cache",
        fake_save_chunk_requirements_cache,
    )
    monkeypatch.setattr(
        extraction_stage,
        "get_tender_extract_prompt",
        lambda **kwargs: ("system", "user"),
    )

    extractor = SimpleNamespace(
        db=object(),
        llm_client=FailingLLM(),
        max_retries=1,
        _extraction_metrics=TenderExtractionMetrics(),
    )
    chunk = SimpleNamespace(
        id=1,
        document_id=2,
        chunk_fingerprint="fingerprint",
        content="任意文本",
        page_num=3,
        section_path="",
    )

    result = await extraction_stage.extract_from_chunk(extractor, chunk)

    assert result == [cached_item]
    assert extractor._extraction_metrics.cache_hits == 1
