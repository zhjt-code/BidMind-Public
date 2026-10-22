"""防御测试: parse_render_profile upsert + 提取任务旧数据清理

覆盖: writing_render_profiles UniqueViolationError
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.writing_render_profile import WritingRenderProfile


class _FakeScalarResult:
    """模拟 db.execute().scalars().first() 链式调用"""

    def __init__(self, value=None):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


def _make_fake_clause(clause_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=clause_id, sub_type="format_rule", content="格式要求", raw_text="")


def _make_llm_client_mock() -> AsyncMock:
    client = AsyncMock()
    client.chat_json = AsyncMock(return_value={
        "font_body": "仿宋_GB2312",
        "font_heading": "黑体",
        "font_size_body": 14.0,
        "line_spacing": 1.5,
    })
    return client


# ==================== parse_render_profile upsert ====================


@pytest.mark.asyncio
async def test_parse_render_profile_insert_when_no_existing() -> None:
    """首次调用: 无已有记录时执行 INSERT"""
    fake_db = SimpleNamespace(
        execute=AsyncMock(return_value=_FakeScalarResult(None)),
        add=MagicMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    from app.core.writer.clause_extractor import ClauseExtractor

    extractor = ClauseExtractor.__new__(ClauseExtractor)
    extractor.db = fake_db
    extractor.llm_client = _make_llm_client_mock()
    extractor.project_id = 100

    clauses = [_make_fake_clause(1001)]
    profile = await extractor.parse_render_profile(100, clauses)

    assert profile is not None
    assert fake_db.add.called, "应调用 db.add 插入新记录"
    fake_db.commit.assert_awaited()
    fake_db.refresh.assert_awaited()


@pytest.mark.asyncio
async def test_parse_render_profile_update_when_existing() -> None:
    """重跑场景: 已有记录时执行 UPDATE 而非 INSERT"""
    existing_profile = WritingRenderProfile(project_id=200)
    existing_profile.id = 42
    existing_profile.font_body = "宋体"
    existing_profile.source_clause_ids = "[]"

    fake_db = SimpleNamespace(
        execute=AsyncMock(return_value=_FakeScalarResult(existing_profile)),
        add=MagicMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    from app.core.writer.clause_extractor import ClauseExtractor

    extractor = ClauseExtractor.__new__(ClauseExtractor)
    extractor.db = fake_db
    extractor.llm_client = _make_llm_client_mock()
    extractor.project_id = 200

    clauses = [_make_fake_clause(2001), _make_fake_clause(2002)]
    profile = await extractor.parse_render_profile(200, clauses)

    assert profile is existing_profile, "应返回已有记录而非新建"
    assert not fake_db.add.called, "不应调用 db.add(已有记录走 UPDATE)"
    assert profile.font_body == "仿宋_GB2312", "字段应被更新"
    assert json.loads(profile.source_clause_ids) == [2001, 2002]
    fake_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_parse_render_profile_returns_none_for_empty_clauses() -> None:
    """无格式约束时应返回 None"""
    from app.core.writer.clause_extractor import ClauseExtractor

    extractor = ClauseExtractor.__new__(ClauseExtractor)
    result = await extractor.parse_render_profile(300, [])
    assert result is None


# ==================== 提取任务旧数据清理(源码静态验证) ====================


def test_extract_task_contains_cleanup_logic() -> None:
    """验证 writing_extract_task 源码包含旧数据清理逻辑

    通过静态检查确认清理代码存在,避免复杂的全链路 mock。
    upsert 测试(上方三个)已覆盖运行时防御。
    """
    import inspect

    from app.core.tasks.writing_extract_task import run_writing_extract_task

    source = inspect.getsource(run_writing_extract_task)

    assert "delete(WritingRenderProfile)" in source, (
        "提取任务应包含 delete(WritingRenderProfile) 清理旧渲染配置"
    )
    assert "delete(WritingClause)" in source, (
        "提取任务应包含 delete(WritingClause) 清理旧约束"
    )
    assert "清理旧提取数据" in source, (
        "清理逻辑应有注释标识"
    )
