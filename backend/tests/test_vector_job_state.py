"""向量化任务状态测试。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.vectorizer.job_state import (
    decide_recovery_from_state,
    enqueue_process_document_job,
    resolve_recovery_count,
    upsert_vector_job_state,
)
from app.models.vector_job_state import (
    ACTIVE_VECTOR_JOB_STATES,
    TERMINAL_VECTOR_JOB_STATES,
    VectorJobState,
    VectorJobStatus,
)


@pytest.mark.asyncio
async def test_enqueue_process_document_job_returns_none_without_pool() -> None:
    """Redis 池不可用时返回 None, 由调用方 is not None 判定。"""
    assert await enqueue_process_document_job(None, project_id=1, document_id=2) is None


@pytest.mark.asyncio
async def test_enqueue_process_document_job_returns_job_id() -> None:
    """正常入队返回 ARQ 的 job_id 字符串 (truthy)。"""
    fake_pool = AsyncMock()
    fake_job = Mock()
    fake_job.job_id = "job-abc-123"
    fake_pool.enqueue_job.return_value = fake_job

    result = await enqueue_process_document_job(
        fake_pool, project_id=1, document_id=2, skip_vl=False
    )

    assert result == "job-abc-123"
    assert bool(result) is True  # 兼容历史 `if result` 用法


@pytest.mark.asyncio
async def test_enqueue_process_document_job_falls_back_on_exception() -> None:
    """enqueue_job 抛异常时返回 None, 保证上层回退 asyncio。"""
    fake_pool = AsyncMock()
    fake_pool.enqueue_job.side_effect = RuntimeError("redis down")

    result = await enqueue_process_document_job(
        fake_pool, project_id=1, document_id=2
    )
    assert result is None


@pytest.mark.asyncio
async def test_upsert_vector_job_state_updates_existing_row() -> None:
    db = AsyncMock()
    existing = VectorJobState(
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.PENDING,
        queue_mode="asyncio",
    )
    result = Mock()
    result.scalar_one_or_none.return_value = existing
    db.execute.return_value = result

    state = await upsert_vector_job_state(
        db,
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.RUNNING,
        queue_mode="arq",
        stage="vectorizing",
        progress="1/10",
        retry_count=1,
    )

    assert state.status == VectorJobStatus.RUNNING
    assert state.queue_mode == "arq"
    assert state.retry_count == 1


@pytest.mark.asyncio
async def test_upsert_vector_job_state_writes_arq_fields() -> None:
    """QUEUED 入口应写入 arq_job_id + enqueued_at 时间锚点。"""
    db = AsyncMock()
    result = Mock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result

    state = await upsert_vector_job_state(
        db,
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.QUEUED,
        queue_mode="arq",
        stage="queued",
        arq_job_id="job-xyz",
        bump_enqueued_at=True,
    )

    assert state.status == VectorJobStatus.QUEUED
    assert state.arq_job_id == "job-xyz"
    assert state.enqueued_at is not None
    assert state.claimed_at is None  # 未显式 bump, 不能覆盖


def test_vector_job_status_enum_uses_lowercase_values() -> None:
    """枚举扩展后应暴露 8 态: pending/queued/claimed/running/completed/failed/lost/recovering"""
    status_type = VectorJobState.__table__.c.status.type
    assert status_type.enums == [
        "pending",
        "queued",
        "claimed",
        "running",
        "completed",
        "failed",
        "lost",
        "recovering",
    ]


def test_active_and_terminal_state_partitions_are_disjoint() -> None:
    """活跃态/终态必须互斥, 避免 gauge 累计错误。"""
    assert ACTIVE_VECTOR_JOB_STATES.isdisjoint(TERMINAL_VECTOR_JOB_STATES)
    covered = ACTIVE_VECTOR_JOB_STATES | TERMINAL_VECTOR_JOB_STATES
    assert covered == set(VectorJobStatus)


# ─── 防御测试: ARQ 状态误判修复 ───────────────────────────────────────────────


def _build_running_state_with_stale_db_heartbeat(stale_seconds: int = 600) -> VectorJobState:
    state = VectorJobState(
        project_id=1,
        document_id=1,
        task_name="process_doc_1",
        status=VectorJobStatus.RUNNING,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )
    state.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    return state


def test_decide_recovery_recovers_when_redis_heartbeat_stale() -> None:
    """场景: ARQ RUNNING + worker 在线 + DB 心跳过期 + 无 Redis 新鲜心跳 -> 必须触发恢复(基线行为)。"""
    state = _build_running_state_with_stale_db_heartbeat()
    with patch(
        "app.core.arq_runtime.has_alive_worker_for", return_value=True
    ):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
        )
    assert should is True
    assert reason == "running_heartbeat_stale"


def test_decide_recovery_short_circuits_when_redis_heartbeat_fresh() -> None:
    """场景: ARQ RUNNING + DB 心跳过期但 Redis 业务心跳仍新鲜 -> 不应触发恢复。

    这是修复"长事务期间 DB 心跳不可见 -> API 误判 running_heartbeat_stale"的核心断言。
    """
    state = _build_running_state_with_stale_db_heartbeat()
    with patch(
        "app.core.arq_runtime.has_alive_worker_for", return_value=True
    ):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=True,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_returns_false_for_completed_state() -> None:
    """COMPLETED 终态永远不需要恢复, 即使 worker 已下线。"""
    state = VectorJobState(
        project_id=1,
        document_id=1,
        task_name="process_doc_1",
        status=VectorJobStatus.COMPLETED,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )
    state.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=600)
    with patch(
        "app.core.arq_runtime.has_alive_worker_for", return_value=False
    ):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
        )
    assert should is False
    assert reason == ""


# ─── 防御测试: 幽灵 QUEUED 恢复判定 ──────────────────────────────────


def _build_queued_state() -> VectorJobState:
    state = VectorJobState(
        project_id=1,
        document_id=1,
        task_name="process_doc_1",
        status=VectorJobStatus.QUEUED,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )
    state.arq_job_id = "process_doc_1"
    return state


def test_decide_recovery_queued_orphaned_when_arq_job_absent() -> None:
    """ 核心: QUEUED + worker 存活 + ARQ 已无 job 痕迹 -> 判定幽灵, 必须恢复。"""
    state = _build_queued_state()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            arq_job_present=False,
        )
    assert should is True
    assert reason == "queued_orphaned"


def test_decide_recovery_queued_healthy_when_arq_job_present() -> None:
    """QUEUED + worker 存活 + ARQ 仍有 job -> 正常排队, 不恢复(防误恢复在途任务)。"""
    state = _build_queued_state()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            arq_job_present=True,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_queued_backward_compat_when_presence_unknown() -> None:
    """未探测 ARQ 存在性(None)时保持原保守行为: worker 存活则不恢复。"""
    state = _build_queued_state()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            arq_job_present=None,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_queued_no_worker_suppressed_when_arq_job_present() -> None:
    """: worker alive 短暂过期但 ARQ 仍有 job -> QUEUED 不应误恢复。"""
    state = _build_queued_state()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=False):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            arq_job_present=True,
        )
    assert should is False
    assert reason == ""


# ─── 防御测试: 恢复计数持久化(MAX_RECOVERY 跨重启生效) ───────────────


def _build_state_with_retry_count(retry_count: Optional[int]) -> VectorJobState:
    state = VectorJobState(
        project_id=1,
        document_id=1,
        task_name="process_doc_1",
        status=VectorJobStatus.RECOVERING,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )
    state.retry_count = retry_count
    return state


def test_resolve_recovery_count_uses_db_when_memory_reset() -> None:
    """ 核心: 内存计数因重启清零(0), DB retry_count=3 -> 取 3, 使 MAX_RECOVERY 仍可触达。"""
    state = _build_state_with_retry_count(3)
    assert resolve_recovery_count(0, state) == 3


def test_resolve_recovery_count_uses_memory_when_larger() -> None:
    """内存计数领先 DB(同进程连续重投尚未落库)-> 取内存值, 不被旧 DB 值拉低。"""
    state = _build_state_with_retry_count(1)
    assert resolve_recovery_count(2, state) == 2


def test_resolve_recovery_count_handles_none_state() -> None:
    """job_state 缺失 -> 退化为内存计数。"""
    assert resolve_recovery_count(1, None) == 1


def test_resolve_recovery_count_handles_null_retry_count() -> None:
    """DB retry_count 为 None(从未重投)-> 视作 0, 取内存计数。"""
    state = _build_state_with_retry_count(None)
    assert resolve_recovery_count(0, state) == 0


# ─── 防御测试: 幽灵 RECOVERING 恢复判定 ──────────────────────────────


def _build_recovering_state_with_stale_heartbeat(stale_seconds: int = 600) -> VectorJobState:
    """构造 RECOVERING + DB 心跳过期 的状态(模拟重投被去重吞没后的卡死现场)。"""
    state = VectorJobState(
        project_id=1,
        document_id=1,
        task_name="process_doc_1",
        status=VectorJobStatus.RECOVERING,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )
    state.arq_job_id = "process_doc_1"
    state.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    return state


def test_decide_recovery_recovering_orphaned_when_arq_job_absent() -> None:
    """ 核心: RECOVERING + worker 存活 + 心跳过期 + ARQ 已无 job -> 幽灵, 必须再次恢复。

 这正是 1252 的现场: 重投撞 keep_result=60s 去重窗口被吞没, 状态写成 RECOVERING 但
 ARQ 实无 job; 旧判据 "hb_stale and not has_worker" 因 worker 在线永不触发 -> 永久卡死。
 """
    state = _build_recovering_state_with_stale_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=False,
        )
    assert should is True
    assert reason == "recovering_orphaned"


def test_decide_recovery_recovering_healthy_when_arq_job_present() -> None:
    """RECOVERING + worker 存活 + 心跳过期 + ARQ 仍有 job -> 重投成功在途, 不恢复(防重复入队)。"""
    state = _build_recovering_state_with_stale_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=True,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_recovering_skips_when_heartbeat_fresh() -> None:
    """RECOVERING + 心跳仍新鲜(刚重投正在被领取) + ARQ 无痕 -> 不恢复, 避免误抢正在领取的任务。

    心跳新鲜由 redis_heartbeat_fresh=True 表达 -> hb_stale=False -> orphaned 判据不成立。
    """
    state = _build_recovering_state_with_stale_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=True,
            arq_job_present=False,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_recovering_backward_compat_when_presence_unknown() -> None:
    """未探测 ARQ 存在性(None) + worker 存活 -> 保持原保守行为(仅 recovering_stuck 路径恢复)。"""
    state = _build_recovering_state_with_stale_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=None,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_recovering_no_worker_suppressed_when_arq_job_present() -> None:
    """: RECOVERING + worker alive 过期但 ARQ 仍在途 -> 不应再次恢复。"""
    state = _build_recovering_state_with_stale_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=False):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=True,
        )
    assert should is False
    assert reason == ""


# ─── 防御测试: RUNNING/CLAIMED in-progress 守卫(假性 stale 不误恢复) ──────


def _build_claimed_state_with_stale_heartbeat(stale_seconds: int = 600) -> VectorJobState:
    state = VectorJobState(
        project_id=1,
        document_id=1,
        task_name="process_doc_1",
        status=VectorJobStatus.CLAIMED,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )
    state.arq_job_id = "process_doc_1"
    state.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    return state


def test_decide_recovery_running_suppressed_when_arq_in_progress() -> None:
    """ 核心: RUNNING + 心跳 stale + ARQ in-progress 在途(present=True) -> 不恢复。

 复现 1481 现场: encode 重 CPU 持 GIL 阻塞事件循环 -> keepalive 推不出心跳 -> 假性 stale,
 但 ARQ in-progress 仍在(任务真活着)。旧逻辑会重投 -> 覆盖活任务心跳致前端进度回跳 +
 制造重复 job。守卫后必须判健康不恢复。
 """
    state = _build_running_state_with_stale_db_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=True,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_running_recovers_when_arq_absent() -> None:
    """RUNNING + 心跳 stale + ARQ 确无痕迹(present=False) -> 真丢失, 恢复。"""
    state = _build_running_state_with_stale_db_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=False,
        )
    assert should is True
    assert reason == "running_heartbeat_stale"


def test_decide_recovery_running_backward_compat_when_presence_unknown() -> None:
    """未探测(None)时保持旧行为: RUNNING + 心跳 stale -> 仍按 stale 恢复(不破坏非探测调用方)。"""
    state = _build_running_state_with_stale_db_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=None,
        )
    assert should is True
    assert reason == "running_heartbeat_stale"


def test_decide_recovery_running_no_worker_suppressed_when_arq_in_progress() -> None:
    """: RUNNING + worker alive 过期但 ARQ in-progress 在途 -> 不恢复。"""
    state = _build_running_state_with_stale_db_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=False):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=True,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_claimed_no_worker_suppressed_when_arq_in_progress() -> None:
    """: CLAIMED + worker alive 过期但 ARQ in-progress 在途 -> 不恢复。"""
    state = _build_claimed_state_with_stale_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=False):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=True,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_claimed_suppressed_when_arq_in_progress() -> None:
    """CLAIMED + 心跳 stale + ARQ in-progress 在途 -> 不恢复(同 RUNNING 守卫)。"""
    state = _build_claimed_state_with_stale_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=True,
        )
    assert should is False
    assert reason == ""


def test_decide_recovery_claimed_recovers_when_arq_absent() -> None:
    """CLAIMED + 心跳 stale + ARQ 确无痕迹 -> 恢复。"""
    state = _build_claimed_state_with_stale_heartbeat()
    with patch("app.core.arq_runtime.has_alive_worker_for", return_value=True):
        should, reason = decide_recovery_from_state(
            state,
            task_active=False,
            heartbeat_timeout_sec=180,
            function_name="process_document_job",
            redis_heartbeat_fresh=False,
            arq_job_present=False,
        )
    assert should is True
    assert reason == "claimed_heartbeat_stale"


@pytest.mark.asyncio
async def test_arq_job_present_returns_true_without_client() -> None:
    """redis 不可用时保守返回 True(无法证伪即视为在途, 不触发误恢复)。"""
    from app.core.arq_runtime import arq_job_present_async

    assert await arq_job_present_async(None, "process_doc_1") is True


@pytest.mark.asyncio
async def test_arq_job_present_false_when_all_keys_absent() -> None:
    """四类 key 全不存在(exists=0) -> 判定 ARQ 已无该 job。"""
    from app.core.arq_runtime import arq_job_present_async

    fake = AsyncMock()
    fake.exists.return_value = 0
    assert await arq_job_present_async(fake, "process_doc_1") is False


@pytest.mark.asyncio
async def test_arq_job_present_true_when_any_key_exists() -> None:
    """存在任一 key(exists>0) -> 视为在途, 不恢复。"""
    from app.core.arq_runtime import arq_job_present_async

    fake = AsyncMock()
    fake.exists.return_value = 1
    assert await arq_job_present_async(fake, "process_doc_1") is True


@pytest.mark.asyncio
async def test_arq_job_present_conservative_on_error() -> None:
    """探测异常时保守返回 True, 避免误判幽灵触发重复入队。"""
    from app.core.arq_runtime import arq_job_present_async

    fake = AsyncMock()
    fake.exists.side_effect = RuntimeError("redis boom")
    assert await arq_job_present_async(fake, "process_doc_1") is True


@pytest.mark.asyncio
async def test_upsert_rejects_downgrade_from_completed_to_recovering() -> None:
    """场景: COMPLETED 已落库, API 恢复路径企图写 RECOVERING -> 必须保持 COMPLETED 不变。

    这是修复"已完成数据被恢复状态覆盖 -> 前端看到任务恢复失败"的核心断言。
    """
    db = AsyncMock()
    completed = VectorJobState(
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.COMPLETED,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )
    completed.stage = "completed"
    completed.progress = "521/521"

    doc_exists_result = Mock()
    doc_exists_result.scalar_one_or_none.return_value = 2
    state_result = Mock()
    state_result.scalar_one_or_none.return_value = completed
    db.execute = AsyncMock(side_effect=[doc_exists_result, state_result])
    db.flush = AsyncMock()

    result = await upsert_vector_job_state(
        db,
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.RECOVERING,
        queue_mode="arq",
        stage="recovering",
        progress="1/3",
        retry_count=1,
    )

    assert result is completed
    assert result.status == VectorJobStatus.COMPLETED
    assert result.stage == "completed"
    assert result.progress == "521/521"
    assert result.retry_count is None  # 未被恢复路径改写
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_rejects_downgrade_from_failed_to_running() -> None:
    """FAILED 终态同样不允许被 RUNNING 拖回活跃态。"""
    db = AsyncMock()
    failed = VectorJobState(
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.FAILED,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )

    doc_exists_result = Mock()
    doc_exists_result.scalar_one_or_none.return_value = 2
    state_result = Mock()
    state_result.scalar_one_or_none.return_value = failed
    db.execute = AsyncMock(side_effect=[doc_exists_result, state_result])
    db.flush = AsyncMock()

    result = await upsert_vector_job_state(
        db,
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.RUNNING,
        queue_mode="arq",
    )

    assert result is failed
    assert result.status == VectorJobStatus.FAILED
    db.flush.assert_not_awaited()


# ─── 防御测试: 超宽写入按列宽截断(失败收尾自免疫) ────────────────────


@pytest.mark.asyncio
async def test_upsert_clamps_overwide_free_text_to_column_width() -> None:
    """ 核心: stage/progress/fallback_reason 超过列宽必须截断落库。

 现场: failure_handler 把 str(error)[:200] 写入 String(128) 的 progress,
 StringDataRightTruncationError 使失败收尾事务整体回滚 -> FAILED 未落库,
 任务假活并可能被 reconciler 反复重投。
 """
    db = AsyncMock()
    doc_exists_result = Mock()
    doc_exists_result.scalar_one_or_none.return_value = 2
    state_result = Mock()
    state_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[doc_exists_result, state_result])
    db.flush = AsyncMock()

    long_error = "E" * 200  # 模拟 failure_handler 的 str(error)[:200]
    long_stage = "s" * 80
    long_reason = "r" * 300

    state = await upsert_vector_job_state(
        db,
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.FAILED,
        queue_mode="arq",
        stage=long_stage,
        progress=long_error,
        fallback_reason=long_reason,
        last_error=long_error,
        bump_finished_at=True,
    )

    columns = VectorJobState.__table__.c
    assert state.progress == long_error[: columns.progress.type.length]
    assert state.stage == long_stage[: columns.stage.type.length]
    assert state.fallback_reason == long_reason[: columns.fallback_reason.type.length]
    assert state.last_error == long_error  # Text 列无宽度限制, 不截断
    assert state.status == VectorJobStatus.FAILED
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_upsert_keeps_short_free_text_and_none_untouched() -> None:
    """边界: 短文本原样保留, None 直通不报错。"""
    db = AsyncMock()
    doc_exists_result = Mock()
    doc_exists_result.scalar_one_or_none.return_value = 2
    state_result = Mock()
    state_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[doc_exists_result, state_result])
    db.flush = AsyncMock()

    state = await upsert_vector_job_state(
        db,
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.RUNNING,
        queue_mode="arq",
        stage="vectorizing",
        progress="1/10",
        fallback_reason=None,
    )

    assert state.stage == "vectorizing"
    assert state.progress == "1/10"
    assert state.fallback_reason is None


@pytest.mark.asyncio
async def test_upsert_allows_completed_idempotent_refresh() -> None:
    """COMPLETED -> COMPLETED 的幂等刷新仍然允许, 例如 worker 重复落地完成态。"""
    db = AsyncMock()
    completed = VectorJobState(
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.COMPLETED,
        queue_mode="arq",
        chunk_fingerprint_version="bge-m3-v1",
    )

    doc_exists_result = Mock()
    doc_exists_result.scalar_one_or_none.return_value = 2
    state_result = Mock()
    state_result.scalar_one_or_none.return_value = completed
    db.execute = AsyncMock(side_effect=[doc_exists_result, state_result])
    db.flush = AsyncMock()

    result = await upsert_vector_job_state(
        db,
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.COMPLETED,
        queue_mode="arq",
        stage="completed",
        progress="521/521",
        bump_finished_at=True,
        bump_heartbeat=True,
    )

    assert result is completed
    assert result.status == VectorJobStatus.COMPLETED
    db.flush.assert_awaited()


# ─── 防御测试: 无状态行时的队列证据消费 ──────────────────────────────


def test_decide_recovery_none_state_suppressed_when_arq_job_present() -> None:
    """: job_state=None 但调用方探测到 ARQ 仍持有确定性 job → 禁止恢复。

 POST 入队后 job_state 落库失败/queued 心跳过期(600s)的窗口内, 队列里的
 job 仍会被 worker 领取; 此时按 legacy_no_state 重投必然与其双执行。
 """
    should, reason = decide_recovery_from_state(
        None,
        task_active=False,
        heartbeat_timeout_sec=180,
        function_name="process_document_job",
        arq_job_present=True,
    )
    assert should is False
    assert reason == ""


def test_decide_recovery_none_state_recovers_when_no_arq_trace() -> None:
    """job_state=None 且 ARQ 确无痕迹 → 仍按 legacy_no_state 恢复(保留兜底能力)。

    写标上传页依赖该路径拉起「POST 瞬态失败」的文档处理, 收窄不得破坏此兜底。
    """
    should, reason = decide_recovery_from_state(
        None,
        task_active=False,
        heartbeat_timeout_sec=180,
        function_name="process_document_job",
        arq_job_present=False,
    )
    assert should is True
    assert reason == "legacy_no_state"


def test_decide_recovery_none_state_unprobed_keeps_legacy_behavior() -> None:
    """调用方未探测(arq_job_present=None)时保持原有 legacy_no_state 行为不变。"""
    should, reason = decide_recovery_from_state(
        None,
        task_active=False,
        heartbeat_timeout_sec=180,
        function_name="process_document_job",
    )
    assert should is True
    assert reason == "legacy_no_state"
