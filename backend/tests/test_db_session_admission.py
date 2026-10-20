""" 防御测试: 进程级 DB 会话许可信号量 + db_session_scope 行为契约.

锁定本次修复的设计约束:
1. 同时打开的 db_session_scope 数量受 DB_SESSION_ADMISSION_LIMIT 硬约束,
 超额请求阻塞直至有人释放, 防止跨 job/跨流程并发协程突发占满 QueuePool。
2. 信号量在 with 块异常退出时仍能正确释放, 不泄漏许可。
3. KnowledgeService.search 路径: safe_encode 必须在 self.db.execute 之前完成,
 保证连接池被持有的"DB 段"不含 GPU 编码这类受外部排队约束的慢 await。

测试均通过 mock 取代真实 DB / GPU 编码, 不依赖任何外部资源。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDbSessionAdmission:
    """db_session_scope 进程级许可信号量行为契约."""

    @pytest.mark.anyio
    async def test_does_not_exceed_admission_limit(self):
        """许可上限 L 时, 同时打开的 scope 数永远 <= L, 超额请求阻塞."""
        from app.core import database as db_module

        # 直接覆写许可上限到一个易观测的小值
        original_sem = db_module._db_session_admission_semaphore
        try:
            db_module._db_session_admission_semaphore = asyncio.Semaphore(3)

            # 用桩 AsyncSessionLocal 取代真实会话, 避免触达 DB
            @asynccontextmanager
            async def _fake_session():
                yield MagicMock()

            concurrent_peak = 0
            currently_open = 0
            lock = asyncio.Lock()
            release_event = asyncio.Event()

            async def worker(idx: int) -> None:
                nonlocal concurrent_peak, currently_open
                with patch.object(db_module, "AsyncSessionLocal", _fake_session):
                    async with db_module.db_session_scope() as _sess:
                        async with lock:
                            currently_open += 1
                            concurrent_peak = max(concurrent_peak, currently_open)
                        # 在 release_event 触发前不释放, 强制堆积
                        await release_event.wait()
                        async with lock:
                            currently_open -= 1

            workers = [asyncio.create_task(worker(i)) for i in range(10)]
            # 等几个调度循环让前 3 个 worker 进入临界区
            for _ in range(20):
                await asyncio.sleep(0)
                async with lock:
                    if currently_open >= 3:
                        break
            # 进入临界区的 worker 不能超过 3
            async with lock:
                assert currently_open == 3, (
                    f"许可上限被突破: currently_open={currently_open}, 期望 <= 3"
                )
            # 释放所有 worker
            release_event.set()
            await asyncio.gather(*workers)

            assert concurrent_peak <= 3, (
                f"全程峰值 {concurrent_peak} 超过许可上限 3"
            )
        finally:
            db_module._db_session_admission_semaphore = original_sem

    @pytest.mark.anyio
    async def test_releases_permit_on_exception(self):
        """with 块内抛异常时, 必须释放许可, 不能泄漏。"""
        from app.core import database as db_module

        original_sem = db_module._db_session_admission_semaphore
        try:
            sem = asyncio.Semaphore(2)
            db_module._db_session_admission_semaphore = sem

            @asynccontextmanager
            async def _fake_session():
                yield MagicMock()

            with patch.object(db_module, "AsyncSessionLocal", _fake_session):
                # 连续 5 次进入并抛异常, 若未释放许可, 第 3 次起会永久阻塞
                for i in range(5):
                    with pytest.raises(RuntimeError, match=f"err-{i}"):
                        async with db_module.db_session_scope():
                            raise RuntimeError(f"err-{i}")

            # 异常后许可必须完全可用 (sem._value 等于初始容量 2)
            assert sem._value == 2, (
                f"异常路径泄漏许可: 当前可用 {sem._value}, 期望 2"
            )
        finally:
            db_module._db_session_admission_semaphore = original_sem

    @pytest.mark.anyio
    async def test_status_snapshot_reflects_in_use(self):
        """db_session_admission_status() 在 scope 内反映 in_use, 退出后归零."""
        from app.core import database as db_module

        original_sem = db_module._db_session_admission_semaphore
        original_limit = db_module._db_session_admission_limit
        try:
            db_module._db_session_admission_semaphore = asyncio.Semaphore(5)
            db_module._db_session_admission_limit = 5

            @asynccontextmanager
            async def _fake_session():
                yield MagicMock()

            with patch.object(db_module, "AsyncSessionLocal", _fake_session):
                async with db_module.db_session_scope():
                    snap = db_module.db_session_admission_status()
                    assert snap.get("lazy") is True
                    assert snap.get("limit") == 5
                    assert snap.get("in_use", 0) >= 1
                snap_after = db_module.db_session_admission_status()
                assert snap_after.get("in_use", 99) == 0
        finally:
            db_module._db_session_admission_semaphore = original_sem
            db_module._db_session_admission_limit = original_limit


class TestKnowledgeSearchEncodeOrdering:
    """KnowledgeService.search: safe_encode 必须在 self.db.execute 之前完成."""

    @pytest.mark.anyio
    async def test_safe_encode_completes_before_db_execute(self):
        """断言调用顺序: safe_encode → self.db.execute, 不可颠倒/交错。"""
        from app.services import knowledge_base_service as kb_module

        call_order: list[str] = []

        # 桩 safe_encode: 记录调用并返回单维向量
        async def fake_safe_encode(texts: Any, **kwargs: Any) -> Any:
            call_order.append("safe_encode")
            arr = MagicMock()
            arr.tolist.return_value = [0.1, 0.2, 0.3]
            return [arr]

        # 桩 self.db: 记录 execute 调用并返回空 rows
        mappings_mock = MagicMock()
        mappings_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.mappings.return_value = mappings_mock

        async def fake_execute(*args: Any, **kwargs: Any) -> Any:
            call_order.append("db_execute")
            return execute_result

        db_mock = MagicMock()
        db_mock.execute = AsyncMock(side_effect=fake_execute)

        svc = kb_module.KnowledgeService(db_mock)

        with patch.object(kb_module, "safe_encode", fake_safe_encode):
            results = await svc.search(
                query="test query",
                owner_id=42,
                allowed_document_ids=[1, 2, 3],
                top_k=5,
                min_similarity=0.5,
            )

        assert results == []
        assert call_order == ["safe_encode", "db_execute"], (
            f"调用顺序错误: {call_order}, 期望 ['safe_encode', 'db_execute']"
        )

    @pytest.mark.anyio
    async def test_vector_search_helper_does_not_call_safe_encode(self):
        """_vector_search_kb_chunks 必须是纯 DB 段, 不触发任何 encode."""
        from app.services import knowledge_base_service as kb_module

        encode_calls: list[Any] = []

        async def tracking_safe_encode(*args: Any, **kwargs: Any) -> Any:
            encode_calls.append((args, kwargs))
            arr = MagicMock()
            arr.tolist.return_value = [0.0]
            return [arr]

        mappings_mock = MagicMock()
        mappings_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.mappings.return_value = mappings_mock

        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=execute_result)

        svc = kb_module.KnowledgeService(db_mock)

        with patch.object(kb_module, "safe_encode", tracking_safe_encode):
            await svc._vector_search_kb_chunks(
                query_embedding=[0.1, 0.2, 0.3],
                effective_owner_id=42,
                applicable_flow="bid_review",
                business_types=None,
                allowed_document_ids=[],
                top_k=5,
                min_similarity=0.5,
            )

        assert encode_calls == [], (
            f"_vector_search_kb_chunks 内不应触发 safe_encode, 实际调用 {len(encode_calls)} 次"
        )
        # 必须有且仅有 1 次 DB 查询
        assert db_mock.execute.await_count == 1, (
            f"应仅执行 1 次 DB 查询, 实际 {db_mock.execute.await_count} 次"
        )


@pytest.fixture
def anyio_backend():
    """anyio 后端固定为 asyncio (与既有测试一致)."""
    return "asyncio"
