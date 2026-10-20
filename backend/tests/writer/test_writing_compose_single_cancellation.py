""" 防御测试: 单章生成必须有取消检查点与 CancelledError 收尾。

职责: 锁定正文返回后的项目取消检查，以及 CancelledError 记痕迹并 re-raise。
不做: 不连接真实数据库、不调用 SectionComposer 真逻辑。
"""

from __future__ import annotations

import ast
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.arq_runtime import ProjectCancelledError
from app.core.tasks.writing_compose_single_task import run_writing_compose_single_task

_SINGLE_TASK_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "core"
    / "tasks"
    / "writing_compose_single_task.py"
)


def _except_type_names(expr: ast.AST) -> set[str]:
    if isinstance(expr, ast.Name):
        return {expr.id}
    if isinstance(expr, ast.Attribute):
        return {expr.attr}
    if isinstance(expr, ast.Tuple):
        names: set[str] = set()
        for elt in expr.elts:
            names |= _except_type_names(elt)
        return names
    return set()


def _top_level_try_handlers(source: str) -> list[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_writing_compose_single_task":
            for stmt in node.body:
                if not isinstance(stmt, (ast.With, ast.AsyncWith)):
                    continue
                for item in stmt.body:
                    if not isinstance(item, ast.Try):
                        continue
                    names: list[str] = []
                    for handler in item.handlers:
                        if handler.type is None:
                            names.append("bare")
                            continue
                        type_names = _except_type_names(handler.type)
                        if "CancelledError" in type_names:
                            names.append("CancelledError")
                        elif "Exception" in type_names:
                            names.append("Exception")
                        else:
                            names.append(next(iter(type_names), "other"))
                    if "CancelledError" in names:
                        return names
    return []


def test_single_task_source_has_cancelled_error_reraise() -> None:
    """当前实现缺少 CancelledError 收尾时本用例必须失败。"""
    source = _SINGLE_TASK_PATH.read_text(encoding="utf-8")
    assert "except asyncio.CancelledError" in source
    assert "record_task_interrupted" in source
    handlers = _top_level_try_handlers(source)
    assert "CancelledError" in handlers
    assert "Exception" in handlers
    assert handlers.index("CancelledError") < handlers.index("Exception")
    assert "raise" in source[source.find("except asyncio.CancelledError"):]



def test_single_task_checks_cancel_after_compose_body() -> None:
    """正文 compose_single 返回后必须再检查项目取消，避免删除后继续写状态。"""
    source = _SINGLE_TASK_PATH.read_text(encoding="utf-8")
    compose_at = source.find("compose_single(")
    cancel_at = source.find("raise_if_project_cancelled", compose_at + 1)
    complete_at = source.find("mark_task_completed", compose_at)
    assert compose_at != -1
    assert cancel_at != -1
    assert complete_at != -1
    assert cancel_at < complete_at


class _FakeDb:
    def __init__(self) -> None:
        self.rollbacks = 0
        self.commits = 0
        self.node = SimpleNamespace(id=9, project_id=4)

    async def get(self, _model: object, node_id: int) -> object:
        return self.node if node_id == 9 else None

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_cancelled_error_is_recorded_and_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = _FakeDb()

    @asynccontextmanager
    async def _session_factory():
        yield fake_db

    monkeypatch.setattr(
        "app.core.tasks.writing_compose_single_task.async_session_factory",
        _session_factory,
    )
    record = AsyncMock()
    mark_running = AsyncMock()
    mark_completed = AsyncMock()
    mark_failed = AsyncMock()

    class _Retry:
        NODE_WRITING_COMPOSE_SINGLE = "writing_compose_single"

        def __init__(self, _db: object) -> None:
            pass

        mark_task_running = mark_running
        mark_task_completed = mark_completed
        mark_task_failed = mark_failed

    class _Composer:
        async def compose_single(self, *_args: object, **_kwargs: object) -> object:
            raise asyncio.CancelledError()

    with (
        patch("app.core.tasks.update_heartbeat"),
        patch("app.core.arq_runtime.is_project_cancelled", return_value=False),
        patch("app.core.arq_runtime.raise_if_project_cancelled"),
        patch("app.services.retry_service.RetryService", _Retry),
        patch(
            "app.core.writer.section_composer.get_section_composer",
            return_value=_Composer(),
        ),
        patch(
            "app.core.tasks.lifecycle.interrupt_contract.record_task_interrupted",
            record,
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_writing_compose_single_task(4, 9)

    record.assert_awaited()
    mark_completed.assert_not_awaited()
    mark_failed.assert_not_awaited()
    assert fake_db.rollbacks >= 1


@pytest.mark.asyncio
async def test_project_cancel_after_compose_skips_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = _FakeDb()

    @asynccontextmanager
    async def _session_factory():
        yield fake_db

    monkeypatch.setattr(
        "app.core.tasks.writing_compose_single_task.async_session_factory",
        _session_factory,
    )
    mark_completed = AsyncMock()
    mark_running = AsyncMock()
    mark_failed = AsyncMock()
    invalidate = AsyncMock()

    class _Retry:
        NODE_WRITING_COMPOSE_SINGLE = "writing_compose_single"

        def __init__(self, _db: object) -> None:
            pass

        mark_task_running = mark_running
        mark_task_completed = mark_completed
        mark_task_failed = mark_failed

    class _Composer:
        async def compose_single(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(id=88)

    def _raise_cancel(_project_id: int) -> None:
        raise ProjectCancelledError(4)

    calls = {"n": 0}

    def _maybe_raise(_project_id: int) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            _raise_cancel(_project_id)

    class _WritingService:
        def __init__(self, _db: object) -> None:
            pass

        _invalidate_render_artifact = invalidate

    with (
        patch("app.core.tasks.update_heartbeat"),
        patch("app.core.arq_runtime.is_project_cancelled", return_value=False),
        patch("app.core.arq_runtime.raise_if_project_cancelled", _maybe_raise),
        patch("app.services.retry_service.RetryService", _Retry),
        patch(
            "app.core.writer.section_composer.get_section_composer",
            return_value=_Composer(),
        ),
        patch("app.services.writing_service.WritingService", _WritingService),
    ):
        await run_writing_compose_single_task(4, 9)

    mark_completed.assert_not_awaited()
    invalidate.assert_not_awaited()
    assert fake_db.commits == 0
