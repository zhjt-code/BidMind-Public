""" 防御测试: collusion 模块读路径必须使用按需短 session。

职责: 验证 extraction_stage / pair_persistence 等读路径在主 self._db
 已失效时仍能通过 async_session_factory 独立完成查询, 锁定"短读 /
 长 LLM 无 session / 短写"模式。
不做: 不验证业务结果正确性、不验证 LLM 调用、不验证写入逻辑、不依赖
 真实数据库。

锁定的修复设计原则 (与 同源):
- AsyncSession 不能跨长 LLM 调用持有连接
- pool_pre_ping 仅保护取连接瞬间, 不保护持有期
- 长任务必须"迟获取、早释放"DB 连接
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.collusion.analyzer_parts import (
    extraction_stage as extraction_stage_mod,
    pair_persistence as pair_persistence_mod,
)


class _FakeAsyncSession:
    """支持 ``async with`` 协议的伪 session, 暴露可断言的 execute mock。"""

    def __init__(self, execute_result):
        self.execute = AsyncMock(return_value=execute_result)
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


def _make_explosive_db() -> MagicMock:
    """构造一个 self._db, 任何对它的 .execute 调用都会失败。

    模拟生产场景: 主 session 连接已被服务端关闭/失效。
    """
    db = MagicMock()
    db.execute = AsyncMock(side_effect=AssertionError(
        "self._db 不应被读路径触碰 -- 必须用按需短 session"
    ))
    return db


class TestCheckExtractionQualityShortSession:
    """check_extraction_quality 必须用独立短 session, 不碰 self._db。"""

    @pytest.mark.anyio
    async def test_short_session_used_when_self_db_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # 模拟数据库返回 0 条失败提取: 函数应静默返回, 不抛任何异常
        result_mock = MagicMock()
        result_mock.fetchall = MagicMock(return_value=[])
        fake_session = _FakeAsyncSession(result_mock)

        monkeypatch.setattr(
            extraction_stage_mod, "async_session_factory",
            lambda: fake_session,
        )

        explosive_db = _make_explosive_db()
        fake_self = SimpleNamespace(_project_id=42, _db=explosive_db)

        bid_docs = [
            SimpleNamespace(id=101, filename="a.pdf"),
            SimpleNamespace(id=102, filename="b.pdf"),
        ]

        # 不应抛错; 短 session 被用过, self._db 一次都没被调用
        await extraction_stage_mod.check_extraction_quality(fake_self, bid_docs)

        assert fake_session.entered is True
        assert fake_session.exited is True
        assert fake_session.execute.await_count == 1
        explosive_db.execute.assert_not_called()


class TestQueryExtractionShortSession:
    """query_extraction 必须用独立短 session, 不碰 self._db。"""

    @pytest.mark.anyio
    async def test_short_session_used_when_self_db_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=None)
        fake_session = _FakeAsyncSession(result_mock)

        monkeypatch.setattr(
            extraction_stage_mod, "async_session_factory",
            lambda: fake_session,
        )

        explosive_db = _make_explosive_db()
        fake_self = SimpleNamespace(_project_id=42, _db=explosive_db)

        result = await extraction_stage_mod.query_extraction(fake_self, doc_id=999)

        assert result is None
        assert fake_session.entered is True
        assert fake_session.exited is True
        assert fake_session.execute.await_count == 1
        explosive_db.execute.assert_not_called()


class TestLoadDocumentsShortSession:
    """load_documents 必须用独立短 session, 不碰 self._db。"""

    @pytest.mark.anyio
    async def test_short_session_used_when_self_db_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        scalars_mock = MagicMock()
        scalars_mock.all = MagicMock(return_value=[])
        result_mock = MagicMock()
        result_mock.scalars = MagicMock(return_value=scalars_mock)
        fake_session = _FakeAsyncSession(result_mock)

        monkeypatch.setattr(
            extraction_stage_mod, "async_session_factory",
            lambda: fake_session,
        )

        explosive_db = _make_explosive_db()
        fake_self = SimpleNamespace(_project_id=42, _db=explosive_db)

        docs = await extraction_stage_mod.load_documents(fake_self)

        assert docs == []
        assert fake_session.entered is True
        assert fake_session.exited is True
        explosive_db.execute.assert_not_called()


class TestQueryPairResultShortSession:
    """query_pair_result 必须用独立短 session, 不碰 self._db。"""

    @pytest.mark.anyio
    async def test_short_session_used_when_self_db_dead(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=None)
        fake_session = _FakeAsyncSession(result_mock)

        monkeypatch.setattr(
            pair_persistence_mod, "async_session_factory",
            lambda: fake_session,
        )

        explosive_db = _make_explosive_db()
        fake_self = SimpleNamespace(_project_id=42, _db=explosive_db)

        result = await pair_persistence_mod.query_pair_result(
            fake_self, doc_a_id=100, doc_b_id=200,
        )

        assert result is None
        assert fake_session.entered is True
        assert fake_session.exited is True
        assert fake_session.execute.await_count == 1
        explosive_db.execute.assert_not_called()


@pytest.fixture
def anyio_backend():
    """统一用 asyncio backend, 避免 trio 依赖。"""
    return "asyncio"
