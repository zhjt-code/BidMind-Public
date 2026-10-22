# -*- coding: utf-8 -*-
"""长阶段心跳保活防御测试。

防御目标(映射真实风险):
- doc1923 实测: 评分项阶段只在入口刷一次心跳、静默 24.5 分钟, 心跳龄越过
 HEARTBEAT_TIMEOUT(600s) 后状态接口把仍在运行的任务误判为已完成。核心断言是
 "长 await 期间心跳被多次刷新", 而非"能跑通"。
- 保活是旁路设施, 绝不能改变被等待协程的结果/异常, 也不能因心跳写入失败
 (Redis 抖动)而让业务任务失败。
- 取消路径不得留下孤儿协程(否则 worker 关闭时挂起)。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks.heartbeat_keepalive import (  # noqa: E402
    DEFAULT_KEEPALIVE_INTERVAL_SEC,
    await_with_task_keepalive,
    current_heartbeat_stage_progress,
)


class _HeartbeatSpy:
    def __init__(self, explode: bool = False) -> None:
        self.calls: list[dict] = []
        self._explode = explode

    def __call__(self, name, *, stage="", progress="", extra=None) -> None:  # noqa: ANN001
        self.calls.append(
            {"name": name, "stage": stage, "progress": progress, "extra": extra}
        )
        if self._explode:
            raise RuntimeError("redis down")


@pytest.mark.asyncio
async def test_keepalive_refreshes_heartbeat_repeatedly_during_long_await():
    """核心防回归: 长 await 期间必须多次刷新, 而非只在入口刷一次。"""
    spy = _HeartbeatSpy()

    async def _slow() -> str:
        await asyncio.sleep(0.25)
        return "done"

    result = await await_with_task_keepalive(
        _slow(),
        task_name="extract_project_2442",
        stage="saving",
        progress="99%|评分项提取中",
        interval=0.05,
        heartbeat=spy,
    )

    assert result == "done"
    assert len(spy.calls) >= 3, (
        f"0.25s/0.05s 期间应刷新多次, 实际 {len(spy.calls)} 次——"
        "只刷一次即为 原始缺陷形态"
    )
    assert all(call["stage"] == "saving" for call in spy.calls)
    assert all(call["progress"] == "99%|评分项提取中" for call in spy.calls)


@pytest.mark.asyncio
async def test_keepalive_marks_extra_so_stale_diagnosis_can_tell_source():
    """刷新须带 keepalive 标记, 便于排障区分"真实进度推进"与"仅保活"。"""
    spy = _HeartbeatSpy()

    await await_with_task_keepalive(
        asyncio.sleep(0, result=1),
        task_name="t",
        stage="saving",
        progress="99%|x",
        interval=0.05,
        heartbeat=spy,
        extra={"project_id": 2442},
    )

    assert spy.calls[0]["extra"] == {"project_id": 2442, "keepalive": True}


@pytest.mark.asyncio
async def test_keepalive_propagates_business_exception_unchanged():
    """被等待协程异常必须原样上抛, 保活不得吞掉或改写。"""
    spy = _HeartbeatSpy()

    async def _boom() -> None:
        raise ValueError("提取失败")

    with pytest.raises(ValueError, match="提取失败"):
        await await_with_task_keepalive(
            _boom(), task_name="t", stage="saving", progress="x",
            interval=0.05, heartbeat=spy,
        )


@pytest.mark.asyncio
async def test_keepalive_write_failure_never_breaks_business():
    """Redis 抖动导致心跳写入抛错时, 业务协程仍须正常完成。"""
    spy = _HeartbeatSpy(explode=True)

    async def _work() -> str:
        await asyncio.sleep(0.02)
        return "ok"

    result = await await_with_task_keepalive(
        _work(), task_name="t", stage="saving", progress="x",
        interval=0.05, heartbeat=spy,
    )

    assert result == "ok"
    assert len(spy.calls) >= 1, "心跳确实被尝试写入过(且抛错被吞)"


@pytest.mark.asyncio
async def test_keepalive_cancellation_leaves_no_orphan_task():
    """外部取消时内部协程必须被取消, 不得成为孤儿(worker 关闭会挂起)。"""
    spy = _HeartbeatSpy()
    started = asyncio.Event()
    observed: dict[str, bool] = {"cancelled": False}

    async def _long() -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            observed["cancelled"] = True
            raise

    task = asyncio.create_task(
        await_with_task_keepalive(
            _long(), task_name="t", stage="saving", progress="x",
            interval=0.05, heartbeat=spy,
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert observed["cancelled"] is True


@pytest.mark.asyncio
async def test_keepalive_callable_progress_reads_latest_each_refresh():
    """: progress 为 callable 时每次刷新再取值, 跟上业务回调最新文案。"""
    spy = _HeartbeatSpy()
    latest = ["85%|评分项提取中"]
    flipped = asyncio.Event()

    async def _work() -> str:
        await asyncio.sleep(0.08)
        latest[0] = "90%|解析评分表 3/5"
        flipped.set()
        await asyncio.sleep(0.12)
        return "ok"

    result = await await_with_task_keepalive(
        _work(),
        task_name="t",
        stage="scoring_extract",
        progress=lambda: latest[0],
        interval=0.05,
        heartbeat=spy,
    )

    assert result == "ok"
    await flipped.wait()
    texts = [call["progress"] for call in spy.calls]
    assert "85%|评分项提取中" in texts
    assert "90%|解析评分表 3/5" in texts


@pytest.mark.asyncio
async def test_keepalive_callable_stage_follows_progress_callback():
    """长 await 跨阶段时保活必须跟上当前 stage，禁止钉死入口阶段。"""
    spy = _HeartbeatSpy()
    latest_stage = ["extracting"]
    latest_progress = ["10%|要求提取"]

    async def _work() -> str:
        await asyncio.sleep(0.08)
        latest_stage[0] = "mandatory_coverage"
        latest_progress[0] = "52%|核验否决与资格条款"
        await asyncio.sleep(0.12)
        return "ok"

    result = await await_with_task_keepalive(
        _work(),
        task_name="t",
        stage=lambda: latest_stage[0],
        progress=lambda: latest_progress[0],
        interval=0.05,
        heartbeat=spy,
    )

    assert result == "ok"
    stages = [call["stage"] for call in spy.calls]
    assert "extracting" in stages
    assert "mandatory_coverage" in stages


def test_default_interval_leaves_margin_under_heartbeat_timeout():
    """默认间隔必须远小于判活阈值, 否则保活本身就可能踩线。"""
    from app.core.config import settings
    from app.core.tasks import HEARTBEAT_TIMEOUT

    assert DEFAULT_KEEPALIVE_INTERVAL_SEC > 0
    assert DEFAULT_KEEPALIVE_INTERVAL_SEC * 10 <= HEARTBEAT_TIMEOUT
    inspect_timeout = int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180))
    assert DEFAULT_KEEPALIVE_INTERVAL_SEC * 3 <= inspect_timeout


def test_current_heartbeat_stage_progress_reads_snapshot(monkeypatch):
    """保活须沿用已有心跳文案；夹具1 审查、夹具2 写标。"""
    snapshots = {
        "review_project_1": {"stage": "步骤4-审查", "progress": "3/12"},
        "writing_compose_1": {"stage": "composing", "progress": "节点 2/8"},
    }

    monkeypatch.setattr(
        "app.core.tasks.get_task_heartbeat",
        lambda name: snapshots.get(name),
    )

    assert current_heartbeat_stage_progress("review_project_1") == (
        "步骤4-审查",
        "3/12",
    )
    assert current_heartbeat_stage_progress("writing_compose_1") == (
        "composing",
        "节点 2/8",
    )


def test_current_heartbeat_stage_progress_empty_when_missing(monkeypatch):
    monkeypatch.setattr("app.core.tasks.get_task_heartbeat", lambda _name: None)
    assert current_heartbeat_stage_progress("review_project_missing") == ("", "")
