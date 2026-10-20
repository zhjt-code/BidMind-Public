"""Vector runtime and indexer focused tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.text_chunker import TextChunk


class _DummyLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def test_vector_indexer_init_does_not_load_default_embedding_model():
    """构造索引器不应预加载 GPU embedding model，避免绕过 admission。"""
    from app.core.vectorizer.indexer import VectorIndexer

    mock_db_session = AsyncMock()
    with patch(
        "app.core.vectorizer.embedding.get_embedding_model",
        side_effect=AssertionError("embedding model must be lazy"),
    ):
        indexer = VectorIndexer(mock_db_session)

    assert indexer.embedding_model is None


@pytest.mark.asyncio
async def test_index_chunks_upserts_and_returns_sorted():
    from app.core.vectorizer.indexer import VectorIndexer
    from app.models.chunk import Chunk

    mock_db_session = AsyncMock()
    mock_db_session.flush = AsyncMock()
    inserted = MagicMock()
    inserted.scalars.return_value.all.return_value = [11, 12]
    cleanup = MagicMock()
    fetched = MagicMock()
    chunk_a = Chunk(
        document_id=1,
        content="A",
        embedding=[0.1] * 1024,
        chunk_type="clause",
        parent_block_id="clause:1",
        section_path="资格要求",
        chunk_fingerprint="fp-1",
        embedding_version="bge-m3-v1",
        page_num=1,
        chunk_index=0,
        char_count=1,
    )
    chunk_a.id = 11
    chunk_b = Chunk(
        document_id=1,
        content="B",
        embedding=[0.1] * 1024,
        chunk_type="clause",
        parent_block_id="clause:2",
        section_path="人员要求",
        chunk_fingerprint="fp-2",
        embedding_version="bge-m3-v1",
        page_num=1,
        chunk_index=1,
        char_count=1,
    )
    chunk_b.id = 12
    fetched.scalars.return_value.all.return_value = [chunk_b, chunk_a]
    mock_db_session.execute.side_effect = [inserted, cleanup, fetched]

    sample_chunks = [
        TextChunk(
            chunk_index=0,
            content="第一条 投标人须提供营业执照和安全生产许可证。",
            char_count=23,
            start_char=0,
            end_char=23,
            metadata={"page_num": 1, "chunk_type": "clause", "parent_block_id": "clause:1", "section_path": "资格要求", "chunk_fingerprint": "fp-1"},
        ),
        TextChunk(
            chunk_index=1,
            content="项目经理需提供建造师证书和社保缴费证明。",
            char_count=20,
            start_char=23,
            end_char=43,
            metadata={"page_num": 1, "chunk_type": "clause", "parent_block_id": "clause:2", "section_path": "人员要求", "chunk_fingerprint": "fp-2"},
        ),
    ]

    # 第八轮 CPU 自适应改造后, indexer 不再持有 encode_lock,
    # safe_encode_batch 内部按设备路由自管锁; 测试无需 mock get_encode_lock。
    with patch(
        "app.core.vectorizer.indexer.safe_encode_batch",
        new=AsyncMock(
            return_value=(
                np.random.randn(2, 1024).astype(np.float32),
                {
                    "degraded_to_cpu": False,
                    "recovery_mode": "thread",
                    "retry_chain": [],
                    "gpu_lock_wait_ms": 0,
                    "cpu_queue_wait_ms": 0,
                },
            )
        ),
    ):
        indexer = VectorIndexer(mock_db_session)
        db_chunks = await indexer.index_chunks(document_id=1, chunks=sample_chunks)

    assert [chunk.chunk_index for chunk in db_chunks] == [0, 1]


@pytest.mark.asyncio
async def test_index_chunks_cancel_check_stops_before_encode():
    """向量化批次开始前若项目已取消, 不应继续提交 embedding encode。"""
    from app.core.arq_runtime import ProjectCancelledError
    from app.core.vectorizer.indexer import VectorIndexer

    mock_db_session = AsyncMock()
    sample_chunks = [
        TextChunk(
            chunk_index=0,
            content="第一条 投标人须提供营业执照。",
            char_count=15,
            start_char=0,
            end_char=15,
            metadata={"page_num": 1, "chunk_fingerprint": "fp-cancel"},
        )
    ]

    def cancel_check() -> None:
        raise ProjectCancelledError(99)

    encode = AsyncMock(
        side_effect=AssertionError("cancelled vectorize must not call safe_encode_batch")
    )
    with patch("app.core.vectorizer.indexer.safe_encode_batch", new=encode):
        indexer = VectorIndexer(mock_db_session)
        with pytest.raises(ProjectCancelledError):
            await indexer.index_chunks(
                document_id=1,
                chunks=sample_chunks,
                cancel_check=cancel_check,
            )

    encode.assert_not_awaited()
    mock_db_session.execute.assert_not_called()


def test_build_index_batches_respects_token_budget():
    from app.core.vectorizer.indexer import VectorIndexer

    texts = ["a" * 2000, "b" * 2000, "c" * 100, "d" * 100]
    batches = VectorIndexer._build_index_batches(
        texts=texts,
        max_batch_size=8,
        token_budget=1200,
        dynamic_batching=True,
        bucketing_enabled=False,
    )
    assert len(batches) >= 2


@pytest.mark.asyncio
async def test_search_uses_safe_encode():
    from app.core.vectorizer.indexer import VectorIndexer
    from app.models.chunk import Chunk

    mock_db_session = AsyncMock()
    vector_query = MagicMock()
    vector_query.fetchall.return_value = [(1, 0.88)]
    chunk_query = MagicMock()
    chunk = Mock(spec=Chunk)
    chunk.id = 1
    chunk_query.scalars.return_value.all.return_value = [chunk]
    mock_db_session.execute.side_effect = [vector_query, chunk_query]

    with patch(
        "app.core.vectorizer.indexer.safe_encode",
        new=AsyncMock(return_value=np.random.randn(1, 1024).astype(np.float32)),
    ):
        indexer = VectorIndexer(mock_db_session)
        results = await indexer.search(query="测试查询", document_id=1)

    assert len(results) == 1
    assert results[0][1] == 0.88
