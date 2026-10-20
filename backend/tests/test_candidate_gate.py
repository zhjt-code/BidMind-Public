import asyncio

import numpy as np
import pytest

import app.core.extractor.tender_extractor_parts.candidate_gate as candidate_gate
import app.core.vectorizer.encode_gate as encode_gate
from app.core.extractor.tender_extractor_parts.candidate_gate import (
    CandidateEmbeddingBuffer,
    build_candidate_groups,
    partition_requirements_by_candidates,
)
from app.schemas.requirement import ExtractedRequirement


@pytest.fixture(autouse=True)
def _isolate_candidate_embed_cache():
    candidate_gate.clear_candidate_embed_cache()
    yield
    candidate_gate.clear_candidate_embed_cache()


def _req(
    description: str,
    *,
    category: str = "TECH",
    keywords: list[str] | None = None,
    canonical_key: str | None = None,
) -> ExtractedRequirement:
    return ExtractedRequirement(
        category=category,
        description=description,
        is_mandatory=False,
        keywords=keywords or [],
        source_pages=[1],
        canonical_key=canonical_key,
    )


def test_candidate_groups_link_semantic_neighbors_and_keep_singletons() -> None:
    requirements = [
        _req("投标人须提供项目实施方案", keywords=["实施方案"]),
        _req("供应商需提交项目实施方案说明", keywords=["实施方案"]),
        _req("投标文件须按要求盖章", category="FORMAT", keywords=["盖章"]),
    ]
    embeddings = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.98, 0.02, 0.0],
            [0.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )

    result = build_candidate_groups(
        requirements,
        embeddings=embeddings,
        similarity_threshold=0.75,
        keyword_jaccard_threshold=0.5,
    )

    assert result.components == [[0, 1]]
    assert result.singletons == [2]
    assert result.semantic_edges >= 1


def test_partition_by_candidates_returns_bypass_and_candidate_groups() -> None:
    requirements = [
        _req("投标人须提供营业执照", category="QUAL", canonical_key="license"),
        _req("供应商应提交营业执照扫描件", category="QUAL", canonical_key="license"),
        _req("投标文件须逐页盖章", category="FORMAT", canonical_key="seal"),
    ]

    partition = partition_requirements_by_candidates(requirements)

    assert [[r.description for r in group] for group in partition.candidate_groups] == [
        ["投标人须提供营业执照", "供应商应提交营业执照扫描件"]
    ]
    assert [r.description for r in partition.bypass_requirements] == ["投标文件须逐页盖章"]


def test_embedding_buffer_recomputes_when_prefetch_snapshot_differs(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_encode(texts: list[str]) -> np.ndarray:
        calls.append(list(texts))
        return np.array([[float(len(text))] for text in texts], dtype=np.float32)

    monkeypatch.setattr(candidate_gate, "_encode_texts_async", fake_encode)

    async def _case() -> np.ndarray:
        buffer = CandidateEmbeddingBuffer()
        buffer.submit([
            _req("投标人应提供营业执照"),
            _req("投标人应提供资质证书"),
        ])
        return await buffer.embeddings_or_none([
            _req("投标人应提供资质证书"),
            _req("投标文件须逐页盖章"),
        ])

    embeddings = asyncio.run(_case())

    assert calls == [
        ["投标人应提供营业执照", "投标人应提供资质证书"],
        ["投标人应提供资质证书", "投标文件须逐页盖章"],
    ]
    assert embeddings.tolist() == [[10.0], [9.0]]


def test_encode_texts_async_routes_through_global_encode_gate(monkeypatch) -> None:
    """ 回归防御: 候选门控编码必须经全局 encode_gate.safe_encode。

 根因: candidate_gate 曾直接调用 embedding 模型 .encode，绕开全进程
 唯一的 encode_lock，多个并发招标要求提取任务同时编码会撞出 CUDA
 ``unspecified launch failure``。此测试锁定"必须调用 safe_encode"这一
 契约，防止未来又绕回直接调用模型的旧模式。
 """
    calls: list[tuple[list[str], int]] = []

    async def fake_safe_encode(texts: list[str], initial_batch_size: int = 32, **_: object) -> np.ndarray:
        calls.append((list(texts), initial_batch_size))
        return np.array([[float(len(text))] for text in texts], dtype=np.float32)

    monkeypatch.setattr(encode_gate, "safe_encode", fake_safe_encode)

    result = asyncio.run(candidate_gate._encode_texts_async(["投标人须提供营业执照"]))

    assert calls == [(["投标人须提供营业执照"], 32)]
    assert result is not None
    assert result.tolist() == [[10.0]]


def test_encode_texts_async_empty_input_skips_encode_gate(monkeypatch) -> None:
    """空输入不应触碰全局 encode_gate（避免无意义占用串行锁）。"""
    called = False

    async def fake_safe_encode(*_args: object, **_kwargs: object) -> np.ndarray:
        nonlocal called
        called = True
        return np.empty((0, 0), dtype=np.float32)

    monkeypatch.setattr(encode_gate, "safe_encode", fake_safe_encode)

    result = asyncio.run(candidate_gate._encode_texts_async([]))

    assert result is None
    assert called is False


def test_embed_cache_hit_skips_second_encode(monkeypatch) -> None:
    """同一文本第二次编码不得再次进入 encode_gate。"""
    calls: list[list[str]] = []

    async def fake_safe_encode(texts: list[str], initial_batch_size: int = 32, **_: object) -> np.ndarray:
        calls.append(list(texts))
        return np.array([[float(len(text))] for text in texts], dtype=np.float32)

    monkeypatch.setattr(encode_gate, "safe_encode", fake_safe_encode)

    first = asyncio.run(candidate_gate._encode_texts_async(["投标人须提供营业执照"]))
    second = asyncio.run(candidate_gate._encode_texts_async(["投标人须提供营业执照"]))

    assert calls == [["投标人须提供营业执照"]]
    assert first is not None and second is not None
    assert first.tolist() == second.tolist() == [[10.0]]
    assert candidate_gate.candidate_embed_cache_size() == 1


def test_embed_cache_subset_then_full_table_only_encodes_miss(monkeypatch) -> None:
    """同形态第二例: 先编类别子集，再编全表，第二次只编 miss。"""
    calls: list[list[str]] = []

    async def fake_safe_encode(texts: list[str], initial_batch_size: int = 32, **_: object) -> np.ndarray:
        calls.append(list(texts))
        return np.array([[float(len(text))] for text in texts], dtype=np.float32)

    monkeypatch.setattr(encode_gate, "safe_encode", fake_safe_encode)

    subset = ["提供营业执照复印件", "提供资质证书"]
    full = ["提供营业执照复印件", "提供资质证书", "投标文件须逐页盖章"]
    asyncio.run(candidate_gate._encode_texts_async(subset))
    result = asyncio.run(candidate_gate._encode_texts_async(full))

    assert calls == [subset, ["投标文件须逐页盖章"]]
    assert result is not None
    assert result.tolist() == [[9.0], [6.0], [9.0]]


def test_embed_cache_fifo_evicts_oldest(monkeypatch) -> None:
    monkeypatch.setattr(candidate_gate, "_CANDIDATE_EMBED_CACHE_MAX", 2)
    calls: list[list[str]] = []

    async def fake_safe_encode(texts: list[str], initial_batch_size: int = 32, **_: object) -> np.ndarray:
        calls.append(list(texts))
        return np.array([[float(len(text))] for text in texts], dtype=np.float32)

    monkeypatch.setattr(encode_gate, "safe_encode", fake_safe_encode)

    asyncio.run(candidate_gate._encode_texts_async(["aa", "bbb"]))
    asyncio.run(candidate_gate._encode_texts_async(["cccc"]))
    asyncio.run(candidate_gate._encode_texts_async(["aa"]))

    assert calls == [["aa", "bbb"], ["cccc"], ["aa"]]
