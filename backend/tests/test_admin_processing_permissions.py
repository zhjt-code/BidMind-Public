"""管理员跨用户处理权限回归测试。

覆盖目标:
- 管理员应能代用户推进项目处理/恢复流程。
- 处理入口不得复用 owner-only 权限语义。
- 管理员代触发计费时,费用归属应保持为项目 owner。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.project import ProjectStatus
from app.models.user import UserRole


class _ScalarResult:
    """最小 SQLAlchemy result 桩。"""

    def __init__(self, value: Any = None, *, scalars: list[Any] | None = None) -> None:
        self.value = value
        self._scalars = list(scalars or [])

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[Any]:
        return self._scalars


class _FakeDb:
    """按顺序返回 execute 结果的异步 DB 桩。"""

    def __init__(self, results: list[_ScalarResult]) -> None:
        self.results = list(results)
        self.commits = 0

    async def execute(self, _stmt: Any) -> _ScalarResult:
        if not self.results:
            raise AssertionError("unexpected execute call")
        return self.results.pop(0)

    async def commit(self) -> None:
        self.commits += 1


def _function_contains_owner_only_gate(path: str, function_name: str) -> bool:
    """检测指定函数内是否仍向权限 helper 传 require_owner=True。"""

    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                for keyword in call.keywords:
                    if (
                        keyword.arg == "require_owner"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        return True
            return False
    raise AssertionError(f"function not found: {path}::{function_name}")


@pytest.mark.parametrize(
    ("path", "function_name"),
    [
        ("backend/app/services/review_service.py", "start_review"),
        ("backend/app/api/routers/reviews.py", "retry_review"),
        ("backend/app/services/requirement_service.py", "create"),
        ("backend/app/services/requirement_service.py", "update"),
        ("backend/app/services/requirement_service.py", "confirm_batch"),
        ("backend/app/services/requirement_service.py", "confirm_all"),
        ("backend/app/services/requirement_service.py", "extract"),
        ("backend/app/api/routers/collusion.py", "start_collusion_analyze"),
        ("backend/app/api/routers/collusion.py", "retry_collusion"),
        ("backend/app/services/writing_service.py", "trigger_extract"),
        ("backend/app/services/writing/outline_service.py", "generate_outline"),
        ("backend/app/services/writing/outline_service.py", "update_outline_node"),
        ("backend/app/services/writing/outline_service.py", "reorder_outline"),
        ("backend/app/services/writing/match_service.py", "trigger_match"),
        ("backend/app/services/writing/binding_service.py", "update_binding"),
        ("backend/app/services/writing/binding_service.py", "confirm_bindings"),
        ("backend/app/services/writing/compose_service.py", "trigger_compose"),
        ("backend/app/services/writing/compose_service.py", "compose_single_node"),
        ("backend/app/services/writing/compose_service.py", "update_draft"),
        ("backend/app/services/writing/compose_service.py", "toggle_lock"),
        ("backend/app/services/writing/material_service.py", "update_material_split_state"),
        ("backend/app/services/writing/material_service.py", "update_material_title"),
        ("backend/app/services/writing/material_service.py", "batch_update_material_titles"),
        ("backend/app/services/writing/material_service.py", "save_material"),
        ("backend/app/services/writing/material_service.py", "process_materials_batch"),
        ("backend/app/services/retry_service.py", "retry_writing_material_process"),
        ("backend/app/services/retry_service.py", "retry_async_writing_node"),
        ("backend/app/services/retry_service.py", "retry_writing_render"),
    ],
)
def test_admin_processing_entrypoints_do_not_use_owner_only_gate(
    path: str,
    function_name: str,
) -> None:
    """处理/恢复入口应使用项目操作权限(admin 或 owner),不能复用 owner-only。"""

    assert not _function_contains_owner_only_gate(path, function_name)


@pytest.mark.asyncio
async def test_admin_start_review_charges_project_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """管理员代用户启动投标审查时,计费预检使用项目 owner_id。"""

    from app.services import review_service as review_service_module
    from app.services.review_service import ReviewService
    from app.billing.services import billing_guard as billing_guard_module

    project = SimpleNamespace(
        id=101,
        owner_id=11,
        status=ProjectStatus.EXTRACTED,
        extraction_warnings=[],
        billing_mode=None,
        billing_hold_id=None,
    )
    db = _FakeDb([
        _ScalarResult(SimpleNamespace(id=501)),
        _ScalarResult(SimpleNamespace(id=601)),
        _ScalarResult(scalars=[SimpleNamespace(id=1)]),
    ])
    service = ReviewService(db)  # type: ignore[arg-type]

    async def _resolve_project(project_id, user, *, require_owner=False):
        assert project_id == project.id
        assert user.role == UserRole.ADMIN
        assert require_owner is False
        return project

    service._get_project_or_raise = _resolve_project  # type: ignore[method-assign]

    charged_user_ids: list[int] = []

    async def _pre_check(_db, _project, user_id):
        charged_user_ids.append(user_id)

    monkeypatch.setattr(
        billing_guard_module.BillingGuard,
        "pre_check",
        staticmethod(_pre_check),
    )
    async def _audit_log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(review_service_module, "audit_log", _audit_log)

    async def _dispatch_background_job(**_kwargs):
        return "test", None

    import app.core.tasks as tasks_module

    monkeypatch.setattr(tasks_module, "dispatch_background_job", _dispatch_background_job)

    admin = SimpleNamespace(id=99, role=UserRole.ADMIN, username="admin")
    result = await service.start_review(project.id, admin)  # type: ignore[arg-type]

    assert result["status"] == "processing"
    assert charged_user_ids == [project.owner_id]


@pytest.mark.asyncio
async def test_admin_trigger_writing_extract_charges_project_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员代用户触发写标约束提取时,计费预检使用项目 owner_id。"""

    from app.services import writing_service as writing_service_module
    from app.services import retry_service as retry_service_module
    from app.services.writing_service import WritingService
    from app.billing.services import billing_guard as billing_guard_module

    project = SimpleNamespace(
        id=202,
        owner_id=22,
        status=ProjectStatus.UPLOADING,
        error_message=None,
        billing_mode=None,
        billing_hold_id=None,
    )
    tender_doc = SimpleNamespace(id=302, parsed=1)
    db = _FakeDb([])
    service = WritingService(db)  # type: ignore[arg-type]

    async def _resolve_project(project_id, user, *, require_owner=False):
        assert project_id == project.id
        assert user.role == UserRole.ADMIN
        assert require_owner is False
        return project

    async def _get_tender_doc(project_id):
        assert project_id == project.id
        return tender_doc

    service._get_project_or_raise = _resolve_project  # type: ignore[method-assign]
    service._get_tender_doc = _get_tender_doc  # type: ignore[method-assign]

    charged_user_ids: list[int] = []

    async def _pre_check(_db, _project, user_id):
        charged_user_ids.append(user_id)

    class _FakeRetryService:
        NODE_WRITING_EXTRACT = "writing_extract"

        def __init__(self, _db) -> None:
            pass

        async def begin_attempt(self, **_kwargs):
            return SimpleNamespace(attempt=7)

        async def attach_dispatch_result(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(
        billing_guard_module.BillingGuard,
        "pre_check",
        staticmethod(_pre_check),
    )
    monkeypatch.setattr(writing_service_module, "is_task_active", lambda _name: False)
    monkeypatch.setattr(retry_service_module, "RetryService", _FakeRetryService)

    async def _dispatch_background_job(**_kwargs):
        return "test", None

    import app.core.tasks as tasks_module

    monkeypatch.setattr(tasks_module, "dispatch_background_job", _dispatch_background_job)

    admin = SimpleNamespace(id=99, role=UserRole.ADMIN, username="admin")
    result = await service.trigger_extract(project.id, admin)  # type: ignore[arg-type]

    assert result["message"] == "约束提取已启动"
    assert charged_user_ids == [project.owner_id]
