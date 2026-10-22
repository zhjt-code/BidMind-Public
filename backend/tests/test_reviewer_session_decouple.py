""" 防御测试: reviewer 在并发协程内不再共享 self.db.

锁定 的修复设计原则:
- AsyncSession 不能跨协程共享
- LLM 调用期间不持有 DB session
- 长循环初始化阶段查询用短 session, 不让主 session 长事务

具体断言:
1. _get_project_owner_id: 缓存命中后不再触发新的 session.execute
2. _collect_chunk_pages_for_pair: 显式传入 session 时只用该 session, 不碰 self.db
3. ReviewEvidenceBundle 含 knowledge_context_text 字段, 让 _review_single_pair
 不再构造 KnowledgeContextBuilder
4. engine_pool_status 函数返回连接池快照, 供异常分支诊断使用
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.ai.reviewer import ReviewOrchestrator
from app.core.review_contracts import ReviewEvidenceBundle


def _make_orchestrator(db_mock):
    """构造一个 reviewer, 跳过真实 LLM client 初始化."""
    return ReviewOrchestrator(
        db=db_mock,
        llm_client=MagicMock(),
        max_retries=1,
        item_timeout=1.0,
        project_id=42,
    )


class TestProjectOwnerIdCache:
    """_get_project_owner_id 必须缓存, 避免每个并发协程重复 execute."""

    @pytest.mark.anyio
    async def test_cache_hit_avoids_repeated_execute(self):
        # 模拟 session.execute 返回 owner_id=7
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none = MagicMock(return_value=7)

        session_mock = MagicMock()
        session_mock.execute = AsyncMock(return_value=scalar_result)

        # 共享 self.db 也用同一 mock, 确保缓存命中后既不调 session 也不调 self.db
        orch = _make_orchestrator(session_mock)

        first = await orch._get_project_owner_id(42, session=session_mock)
        second = await orch._get_project_owner_id(42, session=session_mock)
        third = await orch._get_project_owner_id(42)  # 无 session, 应也走缓存

        assert first == 7
        assert second == 7
        assert third == 7
        # 关键断言: 三次调用只触发一次 execute, 缓存生效
        assert session_mock.execute.await_count == 1, (
            f"owner_id 缓存失效, execute 被调 {session_mock.execute.await_count} 次"
        )

    @pytest.mark.anyio
    async def test_owner_id_none_is_cached_too(self):
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none = MagicMock(return_value=None)
        session_mock = MagicMock()
        session_mock.execute = AsyncMock(return_value=scalar_result)

        orch = _make_orchestrator(session_mock)
        first = await orch._get_project_owner_id(42, session=session_mock)
        second = await orch._get_project_owner_id(42, session=session_mock)
        assert first is None
        assert second is None
        assert session_mock.execute.await_count == 1


class TestCollectChunkPagesSessionParam:
    """_collect_chunk_pages_for_pair 显式接收 session 时不碰 self.db."""

    @pytest.mark.anyio
    async def test_passed_session_used_self_db_untouched(self):
        # 构造 BidResponseChunk 查询返回的 page_num 行
        rows_result = MagicMock()
        rows_result.all = MagicMock(return_value=[(1,), (2,), (5,)])

        passed_session = MagicMock()
        passed_session.execute = AsyncMock(return_value=rows_result)

        self_db_mock = MagicMock()
        self_db_mock.execute = AsyncMock(side_effect=AssertionError(
            "self.db 不应被调用 -- 应使用显式传入的 session"
        ))

        orch = _make_orchestrator(self_db_mock)

        pair = SimpleNamespace(
            response=SimpleNamespace(id=99, document_id=10),
        )

        pages = await orch._collect_chunk_pages_for_pair(pair, session=passed_session)
        assert pages == {1, 2, 5}
        # passed_session.execute 被调用一次, self.db 一次都没被调用
        assert passed_session.execute.await_count == 1
        self_db_mock.execute.assert_not_called()


class TestEvidenceBundleKnowledgeContext:
    """ReviewEvidenceBundle 必须支持 knowledge_context_text 字段, 让
    _review_single_pair 在 LLM 调用期间无需自建 KnowledgeContextBuilder."""

    def test_evidence_bundle_has_knowledge_context_text_field(self):
        bundle = ReviewEvidenceBundle(
            response_content="resp",
            source_pages=[1],
            completeness="full",
            missing_elements=[],
            linked_to=None,
            evidence_type=None,
            evidence_anchor=None,
            relevance_score=None,
            relevance_note=None,
            image_evidence="",
            cross_category_evidence="",
            quality_flags=[],
            knowledge_context_text="kb context",
        )
        assert bundle.knowledge_context_text == "kb context"

    def test_evidence_bundle_default_empty_kb_context(self):
        bundle = ReviewEvidenceBundle(
            response_content="",
            source_pages=[],
            completeness=None,
            missing_elements=[],
            linked_to=None,
            evidence_type=None,
            evidence_anchor=None,
            relevance_score=None,
            relevance_note=None,
            image_evidence="",
            cross_category_evidence="",
            quality_flags=[],
        )
        # 默认空串, 不影响下游 prompt 拼接
        assert bundle.knowledge_context_text == ""


class TestEnginePoolStatus:
    """engine_pool_status 函数必须返回连接池快照, 异常时仍可降级."""

    def test_engine_pool_status_returns_dict(self):
        from app.core.database import engine_pool_status

        snapshot = engine_pool_status()
        assert isinstance(snapshot, dict)
        # status_text 必须存在 (无论 QueuePool 还是其他实现)
        assert "status_text" in snapshot

    def test_engine_pool_status_includes_capacity_for_queuepool(self):
        from app.core.database import engine, engine_pool_status

        snapshot = engine_pool_status()
        # AsyncAdaptedQueuePool 应同时暴露 size/checkedin/checkedout
        # NullPool 等无连接池场景仅验证 status_text 不为空
        if hasattr(engine.pool, "size"):
            assert "size" in snapshot
            assert "checkedout" in snapshot


@pytest.fixture
def anyio_backend():
    """统一用 asyncio backend, 避免 trio 依赖."""
    return "asyncio"
