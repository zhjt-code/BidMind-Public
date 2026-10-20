"""管理员强制重新提取 Service 的防御测试。

职责: 锁定重提取服务的清理边界与任务入队语义。
不做: 不连接数据库、不执行后台任务、不调用 LLM。
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.exceptions import ForbiddenError  # noqa: E402
from app.models.project import ProjectStatus, ProjectType  # noqa: E402
from app.models.user import UserRole  # noqa: E402


def _service_class():
    from app.services import requirement_reextract_service as module

    service_class = getattr(module, "RequirementReextractService", None)
    assert service_class is not None, "重提取服务应去管理员限定命名"
    return service_class


class _FakeDb:
    def __init__(self, project_owner_id: int) -> None:
        self.project = SimpleNamespace(
            id=2417,
            owner_id=project_owner_id,
            project_type=ProjectType.BID_REVIEW,
            status=ProjectStatus.EXTRACTED,
            error_message="old error",
            extraction_warnings=["old warning"],
        )
        self._scalar_values = [
            self.project,
            SimpleNamespace(id=901),
            1,
        ]
        self.commits = 0

    async def scalar(self, _stmt):  # noqa: ANN001
        return self._scalar_values.pop(0)

    async def commit(self) -> None:
        self.commits += 1


def _user(user_id: int, role: UserRole) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, username=f"user-{user_id}", role=role)


def test_reextract_service_reuses_parsed_documents_and_clears_review_outputs():
    """Service 必须复用已解析文档，并委托 swap 模块清空下游审查产物（后架构）。

 P0-2 修复后：清理逻辑由 requirement_reextract_swap 模块承担，
 Service 只做薄委托；审查产物清理仍覆盖（通过 swap 模块），但
 不再在 Service 源码中直接引用 ReviewFact/BidResponse 等模型。
 """
    source = inspect.getsource(_service_class())

    assert "Document.parsed == 1" in source
    assert "process_document_job" not in source
    # 清理逻辑已委托 swap 模块——Service 通过 clear_extracted_artifacts 间接清理
    assert "clear_extracted_artifacts" in source
    assert "count_existing_outputs" in source
    assert "ProjectStatus.EXTRACTING" in source
    assert "dispatch_background_job" in source
    assert "extract_requirements_job" in source
    assert "仅管理员" not in source


@pytest.mark.asyncio
async def test_project_owner_user_can_reextract_from_parsed_documents(monkeypatch):
    """普通项目所有者应可从已解析文档重新提取招标要求。"""
    from app.services import requirement_reextract_service as module

    fake_db = _FakeDb(project_owner_id=7)
    monkeypatch.setattr(module, "is_task_active", lambda _task_name: False)
    monkeypatch.setattr(
        module,
        "count_existing_outputs",
        AsyncMock(
            return_value={
                "requirements": 3,
                "bid_responses": 2,
                "review_results": 1,
                "review_facts": 4,
            }
        ),
    )
    clear_mock = AsyncMock()
    monkeypatch.setattr(module, "clear_extracted_artifacts", clear_mock)
    monkeypatch.setattr(module, "audit_log", AsyncMock())
    dispatch_mock = AsyncMock(return_value=("arq", "job-1"))
    monkeypatch.setattr(module, "dispatch_background_job", dispatch_mock)

    result = await _service_class()(fake_db).reextract_from_parsed_documents(
        project_id=2417,
        current_user=_user(7, UserRole.USER),
    )

    assert result["status"] == "started"
    assert fake_db.project.status == ProjectStatus.EXTRACTING
    assert fake_db.project.error_message is None
    assert fake_db.project.extraction_warnings is None
    clear_mock.assert_awaited_once()
    dispatch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_owner_user_cannot_reextract(monkeypatch):
    """非项目所有者的普通用户不得重新提取他人项目。"""
    from app.services import requirement_reextract_service as module

    monkeypatch.setattr(module, "is_task_active", lambda _task_name: False)
    fake_db = _FakeDb(project_owner_id=7)

    with pytest.raises(ForbiddenError):
        await _service_class()(fake_db).reextract_from_parsed_documents(
            project_id=2417,
            current_user=_user(8, UserRole.USER),
        )


@pytest.mark.asyncio
async def test_admin_can_reextract_other_user_project(monkeypatch):
    """管理员仍可重新提取他人项目，保留运维能力。"""
    from app.services import requirement_reextract_service as module

    fake_db = _FakeDb(project_owner_id=7)
    monkeypatch.setattr(module, "is_task_active", lambda _task_name: False)
    monkeypatch.setattr(
        module,
        "count_existing_outputs",
        AsyncMock(
            return_value={
                "requirements": 0,
                "bid_responses": 0,
                "review_results": 0,
                "review_facts": 0,
            }
        ),
    )
    monkeypatch.setattr(module, "clear_extracted_artifacts", AsyncMock())
    monkeypatch.setattr(module, "audit_log", AsyncMock())
    monkeypatch.setattr(
        module,
        "dispatch_background_job",
        AsyncMock(return_value=("arq", "job-admin")),
    )

    result = await _service_class()(fake_db).reextract_from_parsed_documents(
        project_id=2417,
        current_user=_user(1, UserRole.ADMIN),
    )

    assert result["status"] == "started"
