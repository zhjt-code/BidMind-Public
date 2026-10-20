"""日志上下文覆盖率防御测试 (P1 级)。

防御目标 (阶段二改造):
- HTTP 路由层: project_log_context 依赖在 path 含 {project_id} 时正确注入 ContextVar
- 后台任务层: observe_task 装饰器自动注入 project_id 到 logger 上下文
- 直接调用场景: ReviewOrchestrator.review_project 入口包裹 with logger.contextualize(project_id=...)
- ContextVar 生命周期: 路径无 project_id 或解析失败时静默, 不影响业务

避免对 DB / vLLM 等外部依赖, 仅验证上下文注入行为。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from app.api.dependencies import project_log_context  # noqa: E402
from app.core.middleware import (  # noqa: E402
    _project_id_ctx,
    get_log_project_id,
    set_log_project_id,
)

# ── ContextVar 基础行为 ──────────────────────────────────────────


def test_set_get_log_project_id_round_trip() -> None:
    """set_log_project_id 写入 ContextVar 后, get_log_project_id 应能读到。"""
    token = _project_id_ctx.set(None)
    try:
        set_log_project_id(123)
        assert get_log_project_id() == 123
        set_log_project_id(456)
        assert get_log_project_id() == 456
    finally:
        _project_id_ctx.reset(token)


def test_get_log_project_id_defaults_to_none_when_not_set() -> None:
    """无任何写入时, get_log_project_id 应返回 None (默认值)。"""
    token = _project_id_ctx.set(None)
    try:
        assert get_log_project_id() is None
    finally:
        _project_id_ctx.reset(token)


# ── project_log_context 依赖: 三种 path_params 场景 ──────────────


def _make_request_with_path_params(path_params: dict) -> MagicMock:
    """构造一个 mock Request 对象, 仅暴露 path_params 属性 (FastAPI 依赖只用这个)。"""
    request = MagicMock()
    request.path_params = path_params
    return request


def test_project_log_context_injects_when_path_has_project_id() -> None:
    """路径含 project_id 时: 依赖正确注入到 ContextVar 并返回该 ID。"""
    token = _project_id_ctx.set(None)
    try:
        request = _make_request_with_path_params({"project_id": "789"})
        result = project_log_context(request)
        assert result == 789
        assert get_log_project_id() == 789
    finally:
        _project_id_ctx.reset(token)


def test_project_log_context_returns_none_when_path_has_no_project_id() -> None:
    """路径不含 project_id 时: 依赖静默返回 None, 不影响 ContextVar 现有值。"""
    token = _project_id_ctx.set(42)  # 预设值
    try:
        request = _make_request_with_path_params({"some_other_id": "1"})
        result = project_log_context(request)
        assert result is None
        # 关键: 依赖不应清空已有值
        assert get_log_project_id() == 42
    finally:
        _project_id_ctx.reset(token)


def test_project_log_context_tolerates_malformed_path_param() -> None:
    """project_id 无法解析为 int 时: 静默返回 None, 不抛异常。"""
    token = _project_id_ctx.set(None)
    try:
        request = _make_request_with_path_params({"project_id": "not-a-number"})
        result = project_log_context(request)
        assert result is None
        assert get_log_project_id() is None
    finally:
        _project_id_ctx.reset(token)


def test_project_log_context_handles_int_path_param() -> None:
    """path_params 中已是 int 的 project_id 也能正确处理 (健壮性)。"""
    token = _project_id_ctx.set(None)
    try:
        request = _make_request_with_path_params({"project_id": 99})
        result = project_log_context(request)
        assert result == 99
        assert get_log_project_id() == 99
    finally:
        _project_id_ctx.reset(token)


# ── observe_task 装饰器: 自动注入 project_id 到 logger 上下文 ─────


@pytest.mark.asyncio
async def test_observe_task_decorator_contextualizes_project_id(monkeypatch) -> None:
    """observe_task 装饰器应在执行被装饰函数时注入 project_id 到 logger 上下文。

    通过 monkeypatch 拦截 record_task_observation 避免触碰 DB,
    并在被装饰函数内部触发一条 ERROR 日志, 检查日志的 extra.project_id。
    """
    from loguru import logger

    from app.core.observability.task_observation import TaskFlow, observe_task

    # 拦截 record_task_observation 避免 DB 访问
    async def _noop_record(**kwargs):
        return None

    monkeypatch.setattr(
        "app.services.task_observation_service.record_task_observation",
        _noop_record,
    )

    captured: dict = {}

    def _sink(message):
        record = message.record
        if record["level"].name in ("ERROR", "WARNING"):
            captured["extra"] = dict(record["extra"])
            captured["message"] = record["message"]

    sink_id = logger.add(_sink, level="WARNING")

    @observe_task(flow=TaskFlow.TENDER, task_type="test_inject")
    async def run_dummy_task(project_id: int) -> None:
        logger.error("dummy failure inside task")

    try:
        await run_dummy_task(project_id=2024)
    finally:
        logger.remove(sink_id)

    assert captured.get("message") == "dummy failure inside task"
    assert captured.get("extra", {}).get("project_id") == 2024


@pytest.mark.asyncio
async def test_observe_task_decorator_handles_missing_project_id(monkeypatch) -> None:
    """被装饰函数未传 project_id 时, 装饰器仍能正常运行, project_id 应为 None。"""
    from loguru import logger

    from app.core.observability.task_observation import TaskFlow, observe_task

    async def _noop_record(**kwargs):
        return None

    monkeypatch.setattr(
        "app.services.task_observation_service.record_task_observation",
        _noop_record,
    )

    captured: dict = {}

    def _sink(message):
        record = message.record
        if record["level"].name == "ERROR":
            captured["extra"] = dict(record["extra"])

    sink_id = logger.add(_sink, level="WARNING")

    @observe_task(flow=TaskFlow.TENDER, task_type="test_no_pid")
    async def run_dummy_task_no_pid() -> None:
        logger.error("no project id")

    try:
        await run_dummy_task_no_pid()
    finally:
        logger.remove(sink_id)

    # extra 中应有 project_id 但值为 None (装饰器内显式 contextualize 会写入键)
    assert "project_id" in captured.get("extra", {})
    assert captured["extra"]["project_id"] is None
