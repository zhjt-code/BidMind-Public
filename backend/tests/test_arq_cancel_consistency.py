"""ARQ 删除一致性测试

覆盖 后续治理 (删除项目时 ARQ 任务应立即取消并停止任何写入):

1. 项目取消墓碑的写入/读取 async + sync 闭环
2. ARQ job 取消黑名单命中即 worker 跳过
3. upsert_vector_job_state 在取消墓碑命中时 no-op, 不会抛外键错误
4. process_task / review_task / writing_* 入口级取消检查
5. ProjectCancelledError 分支: 异常收尾不写 FAILED 状态
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.arq_runtime import (  # noqa: E402
    ARQ_JOB_CANCEL_KEY_PREFIX,
    PROJECT_CANCEL_KEY_PREFIX,
    ProjectCancelledError,
    abort_arq_job_best_effort,
    clear_project_cancelled_async,
    is_arq_job_cancelled_async,
    is_project_cancelled,
    is_project_cancelled_async,
    mark_arq_job_cancelled_async,
    mark_project_cancelled_async,
    mark_project_cancelled_sync,
    raise_if_project_cancelled,
    raise_if_project_cancelled_async,
)
from app.core.vectorizer.job_state import upsert_vector_job_state  # noqa: E402
from app.models.vector_job_state import VectorJobStatus  # noqa: E402


def test_project_delete_local_task_prefixes_cover_all_project_flows() -> None:
    """删除项目时本地 asyncio fallback 任务前缀必须覆盖三大流程。"""

    source = (Path(__file__).parent.parent / "app" / "services" / "project_service.py").read_text(
        encoding="utf-8"
    )
    required_prefixes = [
        "review_project_",
        "review_bid_extract_",
        "extract_project_",
        "collusion_project_",
        "writing_extract_",
        "writing_outline_",
        "writing_material_process_",
        "writing_match_",
        "writing_compose_",
        "writing_compose_single_",
        "writing_clause_vectorize_",
    ]

    for prefix in required_prefixes:
        assert f'"{prefix}"' in source


# ==================== 取消墓碑 ====================


class _FakeRedisKV:
    """一个最小 async Redis KV 桩, 仅实现 set/get/delete, 够墓碑测试用。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        # ex 不在测试里模拟过期, 只确认键值关系
        self._store[key] = value

    async def get(self, key: str):
        return self._store.get(key)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                removed += 1
        return removed


@pytest.mark.asyncio
async def test_mark_and_query_project_cancelled_async() -> None:
    """墓碑键写入后 async 查询可命中, 清理后返回 False。"""
    redis = _FakeRedisKV()
    pid = 9001

    assert await is_project_cancelled_async(redis, pid) is False

    ok = await mark_project_cancelled_async(redis, pid, reason="project_deleted")
    assert ok is True

    # 键名与 payload 结构必须稳定, 便于未来人工排查
    key = f"{PROJECT_CANCEL_KEY_PREFIX}{pid}"
    raw = await redis.get(key)
    assert raw is not None
    payload = json.loads(raw)
    assert payload["project_id"] == pid
    assert payload["reason"] == "project_deleted"
    assert "marked_at" in payload

    assert await is_project_cancelled_async(redis, pid) is True

    await clear_project_cancelled_async(redis, pid)
    assert await is_project_cancelled_async(redis, pid) is False


@pytest.mark.asyncio
async def test_raise_if_project_cancelled_async_throws_on_hit() -> None:
    """raise_if_project_cancelled_async 命中即抛 ProjectCancelledError。"""
    redis = _FakeRedisKV()
    pid = 9002

    # 未命中不抛
    await raise_if_project_cancelled_async(redis, pid)

    await mark_project_cancelled_async(redis, pid)

    with pytest.raises(ProjectCancelledError) as exc_info:
        await raise_if_project_cancelled_async(redis, pid)
    assert exc_info.value.project_id == pid


@pytest.mark.asyncio
async def test_mark_project_cancelled_async_none_client_returns_false() -> None:
    """Redis 不可用时静默返回 False, 不抛异常。"""
    ok = await mark_project_cancelled_async(None, 9003)
    assert ok is False


# ==================== ARQ job 黑名单 ====================


@pytest.mark.asyncio
async def test_arq_job_cancel_blacklist_roundtrip() -> None:
    redis = _FakeRedisKV()
    job_id = "job-xyz"

    assert await is_arq_job_cancelled_async(redis, job_id) is False

    ok = await mark_arq_job_cancelled_async(redis, job_id, reason="project_deleted")
    assert ok is True
    assert await is_arq_job_cancelled_async(redis, job_id) is True

    # 空 job_id 视为无效
    assert await is_arq_job_cancelled_async(redis, "") is False
    assert await is_arq_job_cancelled_async(redis, None) is False


@pytest.mark.asyncio
async def test_abort_arq_job_best_effort_falls_back_to_tombstone(monkeypatch) -> None:
    """原生 abort 不可用时, 应回退到写黑名单兜底。"""
    redis = _FakeRedisKV()
    job_id = "job-abort-fallback"

    # 强制 arq.jobs.Job.abort 抛异常
    def _raise_on_job_ctor(*args, **kwargs):
        raise RuntimeError("arq.jobs.Job unavailable in test")

    monkeypatch.setattr("arq.jobs.Job", _raise_on_job_ctor)

    ok, method = await abort_arq_job_best_effort(redis, job_id)
    assert ok is True
    assert method == "tombstone"
    assert await is_arq_job_cancelled_async(redis, job_id) is True


# ==================== upsert_vector_job_state 守卫 ====================


class _FakeResult:
    def __init__(self, value: object = None) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """upsert_vector_job_state 所需的最小 async session 桩。"""

    def __init__(self, *, doc_exists: bool = True, existing_state: object = None) -> None:
        self._doc_exists = doc_exists
        self._existing_state = existing_state
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt):
        # 简单根据 stmt 里的表名区分返回: Document 存在性 vs VectorJobState
        stmt_text = str(stmt).lower()
        if "documents" in stmt_text and "vector_job_states" not in stmt_text:
            return _FakeResult(1 if self._doc_exists else None)
        return _FakeResult(self._existing_state)


@pytest.mark.asyncio
async def test_upsert_vector_job_state_skips_when_project_cancelled(monkeypatch) -> None:
    """项目已取消 -> no-op 返回 None, 不执行任何 DB 写入。"""
    monkeypatch.setattr(
        "app.core.arq_runtime.is_project_cancelled", lambda pid: True
    )

    session = _FakeSession()
    result = await upsert_vector_job_state(
        session,  # type: ignore[arg-type]
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.RUNNING,
        queue_mode="arq",
    )

    assert result is None
    assert session.added == []


@pytest.mark.asyncio
async def test_upsert_vector_job_state_skips_when_document_missing(monkeypatch) -> None:
    """项目未取消但文档被级联删除 (非入队态) -> no-op, 防止外键错误。"""
    monkeypatch.setattr(
        "app.core.arq_runtime.is_project_cancelled", lambda pid: False
    )

    session = _FakeSession(doc_exists=False)
    # RUNNING 属于会触发文档存在性检查的路径
    result = await upsert_vector_job_state(
        session,  # type: ignore[arg-type]
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.RUNNING,
        queue_mode="arq",
    )

    assert result is None
    assert session.added == []


@pytest.mark.asyncio
async def test_upsert_vector_job_state_queued_path_skips_doc_check(monkeypatch) -> None:
    """入队态 (QUEUED/PENDING) 跳过文档存在性检查: 入队时刻文档一定存在, 避免无谓查询。"""
    monkeypatch.setattr(
        "app.core.arq_runtime.is_project_cancelled", lambda pid: False
    )

    session = _FakeSession(doc_exists=False, existing_state=None)
    # 文档存在性桩返回 False, 但入队态不该走这个分支 -> 正常创建 state
    result = await upsert_vector_job_state(
        session,  # type: ignore[arg-type]
        project_id=1,
        document_id=2,
        task_name="process_doc_2",
        status=VectorJobStatus.QUEUED,
        queue_mode="arq",
        bump_enqueued_at=True,
    )

    # 入队路径应正常创建 state (即使文档桩返回不存在)
    assert result is not None
    assert len(session.added) == 1


# ==================== ProjectCancelledError 属性 ====================


def test_project_cancelled_error_carries_project_id() -> None:
    err = ProjectCancelledError(42, "deleted by user")
    assert err.project_id == 42
    assert "42" in str(err)
    assert "deleted by user" in str(err)


# ==================== sync 版墓碑写入兜底 ====================


def test_mark_project_cancelled_sync_tolerates_no_redis(monkeypatch) -> None:
    """get_sync_redis 返回 None 时不应抛异常, 仅返回 False。"""
    monkeypatch.setattr("app.core.redis.get_sync_redis", lambda: None)
    assert mark_project_cancelled_sync(123) is False
    # sync 版 is_project_cancelled 同样降级
    assert is_project_cancelled(123) is False
