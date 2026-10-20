""" RED: 默认重匹配绑定决策持久化策略测试

背景:
- run_writing_match_task(project_id, explicit_retry=False) 为默认重匹配路径
- 期望: 默认重匹配只删除 suggested 绑定，保留 confirmed 和 rejected
- 当前行为: explicit_retry=False 时无 status 过滤，删除 ALL 绑定（含 confirmed/rejected）

验证策略:
- 捕获 SQLAlchemy Delete 对象，通过 whereclause.compile 检查 WHERE 子句是否包含
 status 列引用，以及操作符方向（默认应为 status == suggested，explicit_retry 为 status != confirmed）
- 不依赖 SQL 参数值的字符串匹配（bound params 不含字面枚举值）
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql as pg

from app.core.tasks.writing_match_task import run_writing_match_task
from app.models.project import ProjectType


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_project(project_id: int = 999) -> SimpleNamespace:
    return SimpleNamespace(
        id=project_id,
        project_type=ProjectType.BID_WRITING,
        status="outlined",
        error_message=None,
    )


class _MockRetryService:
    """RetryService mock，含 NODE_WRITING_MATCH 类属性以支持 RetryService.NODE_WRITING_MATCH 调用。"""
    NODE_WRITING_MATCH = "writing_match"

    def __init__(self, db):
        self.mark_task_running = AsyncMock()
        self.mark_task_completed = AsyncMock()
        self.mark_task_failed = AsyncMock()
        self.begin_attempt = AsyncMock()
        self.attach_dispatch_result = AsyncMock()


def _compile_whereclause(stmt) -> str:
    """将 SQLAlchemy Delete 语句的 whereclause 编译为 SQL 字符串（PostgreSQL 方言）。

    返回形如 'writing_bindings.project_id = %(project_id_1)s' 的字符串，
    或含 AND/status 的多条件字符串（explicit_retry 路径）。
    """
    wc = getattr(stmt, "whereclause", None)
    if wc is None:
        return ""
    compiled = wc.compile(dialect=pg.dialect())
    return str(compiled)


def _setup_monkeypatch(monkeypatch: pytest.MonkeyPatch, project: SimpleNamespace, captured_stmts: list) -> SimpleNamespace:
    """公共 monkeypatch 设置，捕获对 writing_bindings 表的 Delete 语句对象。"""
    delete_result = SimpleNamespace(rowcount=2)

    async def _capture_execute(stmt, *args, **kwargs):
        # 通过 table.name 准确识别 WritingBinding 的 Delete 语句
        table_name = getattr(getattr(stmt, "table", None), "name", "")
        if table_name == "writing_bindings":
            captured_stmts.append(stmt)
        return delete_result

    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=project),
        commit=AsyncMock(),
        execute=AsyncMock(side_effect=_capture_execute),
        rollback=AsyncMock(),
    )

    monkeypatch.setattr(
        "app.core.tasks.writing_match_task.async_session_factory",
        lambda: _AsyncSessionContext(fake_db),
    )
    monkeypatch.setattr(
        "app.core.tasks.update_heartbeat",
        lambda _task_name, stage="", progress="", **_kw: None,
    )
    # is_project_cancelled 在函数内部 import 自 app.core.arq_runtime；
    # patch 源模块在调用前生效，monkeypatch 路径正确，无需修改。
    monkeypatch.setattr(
        "app.core.arq_runtime.is_project_cancelled",
        lambda _pid: False,
    )
    monkeypatch.setattr(
        "app.services.writing_service.WritingService._vectorize_outline_nodes",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.core.writer.material_matcher.get_material_matcher",
        lambda **_kw: SimpleNamespace(match=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        "app.core.ai.release_project_binding",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.retry_service.RetryService",
        _MockRetryService,
    )
    return fake_db


@pytest.mark.asyncio
async def test_default_rematch_should_preserve_confirmed_and_rejected_only_delete_suggested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: 默认 explicit_retry=False 重匹配应只删除 suggested，保留 confirmed 和 rejected。

 当前实现的 DELETE WHERE 子句:
 writing_bindings.project_id = %(project_id_1)s
 缺少 status 列条件 → confirmed 和 rejected 均被删除。

 修复后 WHERE 子句应为:
 writing_bindings.project_id = %(project_id_1)s AND writing_bindings.status = %(status_1)s
 其中 status_1 绑定到 'suggested'（等值过滤，只删 suggested）。
 """
    project = _make_project(999)
    captured_stmts: list = []
    _setup_monkeypatch(monkeypatch, project, captured_stmts)

    await run_writing_match_task(999, explicit_retry=False)

    # 确认 DELETE 被执行过
    assert len(captured_stmts) >= 1, "期望至少执行一次针对 writing_bindings 的 DELETE"

    wc_sql = _compile_whereclause(captured_stmts[0])

    # RED: 当前 WHERE 仅含 project_id，无 status 列引用。
    # 修复后应通过: WHERE 须含 'status' 列（等值限制 suggested）。
    assert "status" in wc_sql, (
        "RED: 默认重匹配 DELETE 的 WHERE 子句应包含 status 列条件，"
        "以限定只删除 suggested 绑定。"
        f"\n当前 WHERE: {wc_sql}"
    )

    # 进一步确认: 默认路径的 status 条件应为等值（==）而非不等（!=）
    # 即 status = 'suggested'，而非 status != 'confirmed'
    assert "!=" not in wc_sql, (
        "RED: 默认路径应使用 status == suggested（等值），"
        "而非 status != confirmed（不等值，仍会删除 rejected）。"
        f"\n当前 WHERE: {wc_sql}"
    )


@pytest.mark.asyncio
async def test_explicit_retry_preserves_confirmed_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GREEN 对照组: explicit_retry=True 路径当前已含 status != confirmed 条件。

    当前 DELETE WHERE 子句:
        writing_bindings.project_id = %(project_id_1)s AND writing_bindings.status != %(status_1)s
    status_1 绑定到 confirmed → 保留 confirmed 绑定，删除其他（含 suggested 和 rejected）。
    """
    project = _make_project(998)
    captured_stmts: list = []
    _setup_monkeypatch(monkeypatch, project, captured_stmts)

    await run_writing_match_task(998, explicit_retry=True)

    assert len(captured_stmts) >= 1, "期望至少执行一次针对 writing_bindings 的 DELETE"

    wc_sql = _compile_whereclause(captured_stmts[0])

    # GREEN: 当前代码 WHERE 已含 status 列
    assert "status" in wc_sql, (
        f"explicit_retry=True 的 DELETE WHERE 应包含 status 条件。当前 WHERE: {wc_sql}"
    )
    # GREEN: 当前代码使用 status != confirmed（不等值保留 confirmed）
    assert "!=" in wc_sql, (
        f"explicit_retry=True 的 DELETE WHERE 应使用 != 操作符（status != confirmed）。当前 WHERE: {wc_sql}"
    )
