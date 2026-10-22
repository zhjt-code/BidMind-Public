"""ARQ 入队宽限窗口的纯函数测试。

覆盖场景:
- 刚 enqueue 写入 queued 心跳后, 状态查询不应判定任务丢失
- 宽限窗口到期后退化为常规恢复判定
- 心跳中缺少 enqueued_at 时仍能基于 last_heartbeat 计算窗口
- is_task_active 与 is_task_in_enqueue_grace 协同下的最终判定语义

这些 bug 曾在历史上反复出现 (review_project_job 重复投递 + uq_review_project_requirement
冲突 + 计费 hold WARNING 刷屏), 这里锁死关键行为防止回归。
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from app.core.tasks import (
    ENQUEUE_GRACE_SEC,
    TASK_STAGE_QUEUED,
    cleanup_task,
    inspect_task_liveness,
    is_task_active,
    is_task_in_enqueue_grace,
    mark_task_queued,
    update_heartbeat,
)


@pytest.fixture()
def task_name() -> Iterator[str]:
    name = "test_enqueue_grace_dummy_task"
    cleanup_task(name)
    yield name
    cleanup_task(name)


def test_mark_task_queued_carries_skip_vl_extra(task_name: str) -> None:
    """: 入队心跳必须能携带跳过声明，供进度组装在 worker 预检前读取。"""
    mark_task_queued(
        task_name,
        queue_mode="asyncio",
        extra={"vl_skipped": True},
    )

    from app.core.tasks import get_task_heartbeat

    hb = get_task_heartbeat(task_name)
    assert hb is not None
    assert hb["stage"] == TASK_STAGE_QUEUED
    assert hb["vl_skipped"] is True


def test_mark_task_queued_sets_queued_stage_with_timestamp(task_name: str) -> None:
    mark_task_queued(
        task_name,
        queue_mode="arq",
        arq_job_id="job-abc",
        project_id=42,
    )

    from app.core.tasks import get_task_heartbeat

    hb = get_task_heartbeat(task_name)
    assert hb is not None
    assert hb["stage"] == TASK_STAGE_QUEUED
    assert hb["queue_mode"] == "arq"
    assert hb["arq_job_id"] == "job-abc"
    assert hb["project_id"] == 42
    assert "enqueued_at" in hb
    assert float(hb["enqueued_at"]) <= time.time() + 1


def test_fresh_queued_heartbeat_treated_as_active(task_name: str) -> None:
    mark_task_queued(task_name, queue_mode="arq")
    assert is_task_active(task_name) is True
    assert is_task_in_enqueue_grace(task_name) is True


def test_grace_window_expires(task_name: str) -> None:
    # 构造一个已超出宽限窗口的 queued 心跳
    update_heartbeat(
        task_name,
        stage=TASK_STAGE_QUEUED,
        progress="",
        extra={
            "queue_mode": "arq",
            "enqueued_at": time.time() - (ENQUEUE_GRACE_SEC + 10),
        },
    )
    assert is_task_in_enqueue_grace(task_name) is False


def test_non_queued_stage_does_not_count_as_grace(task_name: str) -> None:
    update_heartbeat(
        task_name,
        stage="步骤2-提取",
        progress="3/10",
        extra={"queue_mode": "arq"},
    )
    assert is_task_in_enqueue_grace(task_name) is False


def test_missing_enqueued_at_falls_back_to_last_heartbeat(task_name: str) -> None:
    # 旧版本心跳没有 enqueued_at 字段, 应允许按 last_heartbeat 推断
    update_heartbeat(
        task_name,
        stage=TASK_STAGE_QUEUED,
        progress="",
        extra={"queue_mode": "arq"},
    )
    assert is_task_in_enqueue_grace(task_name) is True


def test_grace_helper_returns_false_when_no_heartbeat(task_name: str) -> None:
    cleanup_task(task_name)
    assert is_task_in_enqueue_grace(task_name) is False


def test_inspect_task_liveness_marks_stale_before_legacy_active_timeout(task_name: str) -> None:
    update_heartbeat(
        task_name,
        stage="extracting",
        progress="32/56",
        extra={
            "queue_mode": "arq",
            "last_heartbeat": time.time() - 20,
        },
    )

    verdict = inspect_task_liveness(task_name, heartbeat_timeout=10)

    assert is_task_active(task_name) is True
    assert verdict.state == "stale"
    assert verdict.active is False
    assert verdict.reason == "heartbeat_stale"


def test_inspect_task_liveness_preserves_enqueue_grace(task_name: str) -> None:
    mark_task_queued(task_name, queue_mode="arq")

    verdict = inspect_task_liveness(task_name, heartbeat_timeout=1)

    assert verdict.state == "enqueue_grace"
    assert verdict.is_live is True


def test_inspect_task_liveness_fresh_arq_waits_unconfirmed_worker_gap(
    task_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """心跳新鲜 + worker 缺失未确认 → 保持 live。"""
    update_heartbeat(
        task_name,
        stage="extracting",
        progress="32/56",
        extra={"queue_mode": "arq"},
    )
    monkeypatch.setattr(
        "app.core.tasks.lifecycle.is_worker_confirmed_missing",
        lambda _fn, **_kw: False,
    )

    verdict = inspect_task_liveness(
        task_name,
        heartbeat_timeout=60,
        arq_function="writing_extract_job",
    )

    assert verdict.state == "live"
    assert verdict.reason == "fresh_heartbeat"


def test_inspect_task_liveness_keeps_fresh_arq_heartbeat_when_worker_missing_confirmed(
    task_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """心跳新鲜时 worker 缺失已确认仍保持 live。"""
    update_heartbeat(
        task_name,
        stage="extracting",
        progress="32/56",
        extra={"queue_mode": "arq"},
    )
    monkeypatch.setattr(
        "app.core.tasks.lifecycle.is_worker_confirmed_missing",
        lambda _fn, **_kw: True,
    )

    verdict = inspect_task_liveness(
        task_name,
        heartbeat_timeout=60,
        arq_function="writing_extract_job",
    )

    assert verdict.state == "live"
    assert verdict.reason == "fresh_heartbeat"
