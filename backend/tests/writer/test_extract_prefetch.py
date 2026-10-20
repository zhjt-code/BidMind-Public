"""写标提取预取：命中即用、失败回退、开关关闭、取消清理。

同形态双夹具：评分收集 + 目录规格（均无数据依赖的长 LLM 子步）。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from app.core.config import settings
from app.core.writer.extract_prefetch import (
    ExtractPrefetchTasks,
    await_directory_prefetch,
    await_scoring_prefetch,
    cancel_extract_prefetch,
    is_extract_prefetch_enabled,
    start_extract_prefetch,
)


@pytest.mark.asyncio
async def test_flag_off_skips_both_prefetch_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "WRITING_EXTRACT_PREFETCH_ENABLED", False)
    assert is_extract_prefetch_enabled() is False
    tasks = start_extract_prefetch(9, 11)
    assert tasks.scoring is None
    assert tasks.directory is None


@pytest.mark.asyncio
async def test_prefetch_hit_returns_scoring_and_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "WRITING_EXTRACT_PREFETCH_ENABLED", True)
    monkeypatch.setattr(settings, "WRITING_SCORING_AUTHORITY_ENABLED", True)

    async def _fake_scoring(project_id: int, document_id: int):
        return ("scoring-outcome", project_id, document_id)

    async def _fake_directory(project_id: int, document_id: int):
        return ("directory-spec", project_id, document_id)

    monkeypatch.setattr(
        "app.core.writer.extract_prefetch._prefetch_scoring_outcome",
        _fake_scoring,
    )
    monkeypatch.setattr(
        "app.core.writer.extract_prefetch._prefetch_directory_spec",
        _fake_directory,
    )

    tasks = start_extract_prefetch(9, 11)
    scoring = await await_scoring_prefetch(tasks)
    directory = await await_directory_prefetch(tasks)
    assert scoring == ("scoring-outcome", 9, 11)
    assert directory == ("directory-spec", 9, 11)


@pytest.mark.asyncio
async def test_prefetch_failure_fail_open_for_both_entities() -> None:
    async def _boom_scoring():
        raise RuntimeError("评分预取失败")

    async def _boom_directory():
        raise RuntimeError("目录预取失败")

    tasks = ExtractPrefetchTasks(
        scoring=asyncio.create_task(_boom_scoring()),
        directory=asyncio.create_task(_boom_directory()),
    )
    assert await await_scoring_prefetch(tasks) is None
    assert await await_directory_prefetch(tasks) is None


@pytest.mark.asyncio
async def test_cancel_extract_prefetch_drops_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "WRITING_EXTRACT_PREFETCH_ENABLED", True)
    monkeypatch.setattr(settings, "WRITING_SCORING_AUTHORITY_ENABLED", True)
    started = asyncio.Event()

    async def _hang(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(
        "app.core.writer.extract_prefetch._prefetch_scoring_outcome",
        _hang,
    )
    monkeypatch.setattr(
        "app.core.writer.extract_prefetch._prefetch_directory_spec",
        _hang,
    )
    tasks = start_extract_prefetch(3, 4)
    await started.wait()
    await cancel_extract_prefetch(tasks)
    assert tasks.scoring is not None and (tasks.scoring.cancelled() or tasks.scoring.done())
    assert tasks.directory is not None and (
        tasks.directory.cancelled() or tasks.directory.done()
    )


def test_extract_task_wires_prefetch_hit_and_inline_fallback() -> None:
    from app.core.tasks.writing_extract_task import run_writing_extract_task

    source = inspect.getsource(run_writing_extract_task)
    assert "start_extract_prefetch" in source
    assert "await_scoring_prefetch" in source
    assert "apply_scoring_authority" in source
    assert "replace_scoring_clauses_with_authority" in source
    assert "await_directory_prefetch" in source
    assert "save_directory_spec" in source
    assert "extract_bid_directory" in source
    assert "cancel_extract_prefetch" in source
    assert settings.WRITING_EXTRACT_PREFETCH_ENABLED is True
