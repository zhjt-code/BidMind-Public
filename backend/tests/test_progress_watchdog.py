""" Wave1-A：业务进展看门狗与长任务调度契约防御测试。"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import patch

import pytest

from app.core.tasks.lifecycle.long_job_registry import (
    LONG_LLM_JOBS,
    resolve_job_timeout,
    resolve_task_name,
)
from app.core.tasks.lifecycle.progress_watchdog import (
    TaskProgressStalledError,
    await_with_progress_guard,
)


async def test_progress_guard_allows_when_fingerprint_keeps_changing() -> None:
    """指纹持续变化时不抛停滞错误。"""
    counter = {"n": 0}

    def _heartbeat(_name: str) -> dict[str, Any]:
        counter["n"] += 1
        return {
            "stage": "extracting",
            "progress": f"step-{counter['n']}",
            "last_heartbeat": float(counter["n"]),
            "extra": {"cursor": counter["n"]},
        }

    async def _work() -> str:
        await asyncio.sleep(0.35)
        return "ok"

    with patch("app.core.tasks.get_task_heartbeat", side_effect=_heartbeat):
        result = await await_with_progress_guard(
            _work(),
            task_name="writing_extract_1",
            max_no_progress_sec=0.2,
            poll_interval=0.05,
        )

    assert result == "ok"


async def test_progress_guard_raises_when_fingerprint_stale() -> None:
    """指纹不变超过阈值时抛 TaskProgressStalledError。"""

    def _heartbeat(_name: str) -> dict[str, Any]:
        return {
            "stage": "extracting",
            "progress": "same",
            "last_heartbeat": 1.0,
            "extra": {"cursor": 1},
        }

    async def _work() -> None:
        await asyncio.sleep(1.0)

    with patch("app.core.tasks.get_task_heartbeat", side_effect=_heartbeat):
        with pytest.raises(TaskProgressStalledError) as exc_info:
            await await_with_progress_guard(
                _work(),
                task_name="writing_extract_2",
                max_no_progress_sec=0.15,
                poll_interval=0.05,
            )

    assert exc_info.value.task_name == "writing_extract_2"
    assert exc_info.value.no_progress_sec >= 0.15


async def test_progress_guard_ignores_last_heartbeat_only_changes() -> None:
    """仅 last_heartbeat / keepalive 变化仍视为无业务进展。"""
    tick = {"n": 0}

    def _heartbeat(_name: str) -> dict[str, Any]:
        tick["n"] += 1
        return {
            "stage": "extracting",
            "progress": "frozen",
            "last_heartbeat": float(tick["n"]),
            "extra": {
                "last_heartbeat": float(tick["n"]),
                "keepalive": tick["n"],
                "batch": 1,
            },
        }

    async def _work() -> None:
        await asyncio.sleep(1.0)

    with patch("app.core.tasks.get_task_heartbeat", side_effect=_heartbeat):
        with pytest.raises(TaskProgressStalledError):
            await await_with_progress_guard(
                _work(),
                task_name="writing_extract_3",
                max_no_progress_sec=0.15,
                poll_interval=0.05,
            )


async def test_progress_guard_rethrows_cancelled_error() -> None:
    """外层 CancelledError 原样重抛。"""

    async def _work() -> None:
        await asyncio.sleep(10.0)

    with patch(
        "app.core.tasks.get_task_heartbeat",
        return_value={"stage": "running", "progress": "x", "extra": {}},
    ):
        guarded = asyncio.create_task(
            await_with_progress_guard(
                _work(),
                task_name="writing_extract_4",
                max_no_progress_sec=30.0,
                poll_interval=0.05,
            )
        )
        await asyncio.sleep(0.05)
        guarded.cancel()
        with pytest.raises(asyncio.CancelledError):
            await guarded


def test_resolve_job_timeout_long_and_default() -> None:
    """LONG 成员走长超时；未知 function 回退短超时。"""
    long_timeout = resolve_job_timeout("writing_extract_job")
    short_timeout = resolve_job_timeout("process_document_job")

    assert "writing_extract_job" in LONG_LLM_JOBS
    assert "extract_requirements_job" in LONG_LLM_JOBS
    assert "review_project_job" in LONG_LLM_JOBS
    assert "writing_compose_job" in LONG_LLM_JOBS
    assert long_timeout == resolve_job_timeout("extract_requirements_job")
    assert long_timeout >= 1800
    assert short_timeout == resolve_job_timeout("unknown_job")
    assert short_timeout <= long_timeout


def test_resolve_task_name_contract() -> None:
    """基础 task_name 契约：写标/审查/串标格式与 compose_single 缺 node_id。"""
    assert resolve_task_name("writing_extract_job", 12) == "writing_extract_12"
    assert resolve_task_name("writing_outline_job", 12) == "writing_outline_12"
    assert resolve_task_name("writing_match_job", 12) == "writing_match_12"
    assert resolve_task_name("writing_compose_job", 12) == "writing_compose_12"
    assert (
        resolve_task_name("writing_compose_single_job", 12, node_id=34)
        == "writing_compose_single_12_34"
    )
    assert resolve_task_name("writing_compose_single_job", 12) is None
    # 与 extract_task.get_extract_task_name / collusion_task.get_collusion_task_name 对齐
    assert resolve_task_name("extract_requirements_job", 12) == "extract_project_12"
    assert resolve_task_name("review_project_job", 12) == "review_project_12"
    assert resolve_task_name("review_bid_extract_job", 12) == "review_bid_extract_12"
    assert resolve_task_name("collusion_analyze_job", 12) == "collusion_project_12"
    assert resolve_task_name("unknown_job", 12) is None


def test_long_llm_jobs_wrapped_with_task_keepalive() -> None:
    """: 长 LLM job 族必须在资源许可后套任务心跳保活，禁止只靠条目边界写心跳。"""
    from app.worker import _run_with_resource_permit

    source = inspect.getsource(_run_with_resource_permit)
    assert "await_with_task_keepalive" in source
    assert "current_heartbeat_stage_progress" in source
    assert "LONG_LLM_JOBS" in source
    keepalive_at = source.find("await_with_task_keepalive")
    jobs_at = source.find("function_name in LONG_LLM_JOBS")
    assert jobs_at != -1 and keepalive_at != -1
    assert jobs_at < keepalive_at, "保活必须落在 LONG_LLM_JOBS 分支内"
