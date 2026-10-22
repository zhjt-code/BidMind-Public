""" 防御测试: 删除项目必须能打断招标提取并 abort 其 ARQ job。

职责: 锁定 extract cancel_check 贯通与删除侧收集 extract heartbeat job_id。
不做: 不启真实 ARQ/DB/LLM，不测完整级联删除性能。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.arq_runtime import ProjectCancelledError  # noqa: E402
from app.core.extractor.tender_extractor import TenderExtractor  # noqa: E402
from app.core.extractor.tender_extractor_parts import merge_category_stage  # noqa: E402
from app.schemas.requirement import ExtractedRequirement  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


def test_report_progress_raises_when_cancel_check_hits() -> None:
    """进度上报必须先执行 cancel_check，命中墓碑即抛 ProjectCancelledError。"""
    extractor = TenderExtractor(db=MagicMock(), llm_client=MagicMock(), concurrency=1)
    extractor._cancel_check = lambda: (_ for _ in ()).throw(
        ProjectCancelledError("project cancelled")
    )

    with pytest.raises(ProjectCancelledError):
        extractor._report_progress("merging", 1, 10, "分类合并中")


@pytest.mark.asyncio
async def test_merge_category_single_pass_checks_cancel_before_llm() -> None:
    """分类合并在每次 LLM 调用前必须检查取消，避免墓碑写入后仍继续 chat_json。"""
    llm = MagicMock()
    llm.chat_json = AsyncMock(return_value=[])
    extractor = TenderExtractor(db=MagicMock(), llm_client=llm, concurrency=1)
    extractor._cancel_check = lambda: (_ for _ in ()).throw(
        ProjectCancelledError("project cancelled")
    )

    reqs = [
        ExtractedRequirement(
            category="COMM",
            description="付款条件示例条目一",
            is_mandatory=True,
            keywords=["付款"],
        ),
        ExtractedRequirement(
            category="COMM",
            description="付款条件示例条目二",
            is_mandatory=True,
            keywords=["付款"],
        ),
    ]

    with pytest.raises(ProjectCancelledError):
        await merge_category_stage.merge_category_single_pass(
            extractor,
            "COMM",
            reqs,
            pass_label="category-single-COMM",
        )

    llm.chat_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_task_passes_cancel_check_into_extractor(monkeypatch) -> None:
    """run_extract_task 必须把 cancel_check 传给 extract_from_document。"""
    from contextlib import asynccontextmanager

    from app.core.tasks import extract_task as extract_task_module
    from app.models.project import ProjectStatus

    project = SimpleNamespace(
        id=3397,
        status=ProjectStatus.EXTRACTING,
        error_message=None,
        extraction_warnings=None,
    )
    tender_doc = SimpleNamespace(id=2445)
    seen: dict[str, object] = {}

    class _SpyExtractor:
        _stage_failures: list = []

        async def extract_from_document(self, *, document_id: int, save_to_db: bool, cancel_check=None):
            seen["document_id"] = document_id
            seen["save_to_db"] = save_to_db
            seen["cancel_check"] = cancel_check
            return []

    class _FakeDb:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    @asynccontextmanager
    async def _fake_session_factory():
        yield _FakeDb()

    monkeypatch.setattr(extract_task_module, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(extract_task_module, "_get_project", AsyncMock(return_value=project))
    monkeypatch.setattr(
        extract_task_module,
        "_get_tender_document",
        AsyncMock(return_value=tender_doc),
    )
    monkeypatch.setattr(
        extract_task_module,
        "get_tender_extractor",
        lambda **_kwargs: _SpyExtractor(),
    )
    monkeypatch.setattr("app.core.tasks.update_heartbeat", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.core.arq_runtime.is_project_cancelled",
        lambda _project_id: False,
    )

    def sentinel_check() -> None:
        return None

    monkeypatch.setattr(
        "app.core.tasks.cancellation.make_project_cancel_check",
        lambda _project_id: sentinel_check,
    )
    monkeypatch.setattr(
        "app.core.config.get_review_concurrency",
        lambda: 2,
    )
    monkeypatch.setattr(
        "app.core.config.settings.TENDER_SCORING_EXTRACT_ENABLED",
        False,
        raising=False,
    )

    await extract_task_module.run_extract_task(project_id=3397)

    assert seen["document_id"] == 2445
    assert seen["cancel_check"] is sentinel_check


@pytest.mark.asyncio
async def test_collect_arq_job_ids_includes_extract_heartbeat(monkeypatch) -> None:
    """删除项目收集 ARQ job 时必须包含 extract_project_* 心跳里的 arq_job_id。"""
    service = ProjectService(db=AsyncMock())

    async def _empty_vector_jobs(*_a, **_k):
        result = MagicMock()
        result.fetchall.return_value = []
        return result

    service.db.execute = AsyncMock(side_effect=_empty_vector_jobs)

    monkeypatch.setattr(
        "app.core.tasks.get_task_heartbeat",
        lambda name: (
            {"arq_job_id": "extract-job-abc"}
            if name == "extract_project_3397"
            else None
        ),
    )

    job_ids = await service._collect_arq_job_ids(3397)
    assert "extract-job-abc" in job_ids
