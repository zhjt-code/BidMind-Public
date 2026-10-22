"""质检当前性: 审查侧只挂起不等待, 在途卷不重复投递, 丢失卷才投递处理队列补算。"""

import asyncio
from types import SimpleNamespace
from typing import Optional

import pytest

from app.core.config import settings
from app.core.tasks.process import document_qa_stage
from app.core.tasks.process.document_qa_finalize import EnvelopeQaDocFact, EnvelopeQaSnapshot
from app.services.document_qa import ensure_current


class _FakeRedis:
    """只实现在途标记用到的三个命令。"""

    def __init__(self) -> None:
        self.ttl: dict[str, Optional[int]] = {}

    def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        self.ttl[key] = ex

    def exists(self, key: str) -> int:
        return 1 if key in self.ttl else 0

    def delete(self, key: str) -> None:
        self.ttl.pop(key, None)


class _NoActiveRunSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr("app.core.redis.get_sync_redis", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _qa_enabled(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_QA_ENABLED", True)
    yield
    for task in list(ensure_current._DETACHED_CURRENCY):
        task.cancel()
    ensure_current._DETACHED_CURRENCY.clear()


def _doc(document_id: int, bid_part: Optional[str]) -> EnvelopeQaDocFact:
    """后台质检尚未落库的卷: 无 document run。"""
    return EnvelopeQaDocFact(
        document_id=document_id,
        bid_part=bid_part,
        parsed=1,
        file_hash=f"fh{document_id}",
        content_hash=f"ch{document_id}",
        run_status=None,
    )


def _install_currency(monkeypatch, docs: list[EnvelopeQaDocFact]) -> list[int]:
    snapshot = EnvelopeQaSnapshot(
        project_exists=True, project_type="bid_review", qa_enabled=True, docs=docs,
    )
    enqueued: list[int] = []

    async def fake_snapshot(_project_id):
        return snapshot

    async def fake_enqueue(_project_id, document_id):
        enqueued.append(document_id)
        return "queued"

    async def fake_finalize(_project_id):
        return SimpleNamespace(
            should_finalize=False, status="skipped", reason="document_runs_not_ready",
        )

    monkeypatch.setattr(
        "app.core.tasks.process.document_qa_finalize._load_snapshot", fake_snapshot,
    )
    monkeypatch.setattr(
        "app.core.tasks.process.document_qa_finalize.maybe_finalize_document_qa_envelope",
        fake_finalize,
    )
    monkeypatch.setattr("app.core.database.async_session_factory", _NoActiveRunSession)
    monkeypatch.setattr("app.core.tasks.document_qa_job.enqueue_document_qa", fake_enqueue)
    return enqueued


# ── 当前性判定: 在途 / 缺失 / 丢失三态 ─────────────────────────────────


async def test_integral_inflight_volume_is_not_enqueued(monkeypatch, fake_redis):
    """本案例夹具: 整本卷质检未落库但仍在途, 当前性检查不重复投递。"""
    enqueued = _install_currency(monkeypatch, [_doc(2544, None)])
    fake_redis.set(document_qa_stage._inflight_key(2544), "1", ex=180)

    status = await ensure_current.ensure_document_qa_current(1627)

    assert status == "in_progress"
    assert enqueued == []


async def test_integral_lost_volume_is_enqueued(monkeypatch, fake_redis):
    """无 run 且无在途标记(legacy 或随进程丢失)才投递到处理队列补算。"""
    enqueued = _install_currency(monkeypatch, [_doc(2544, None)])

    status = await ensure_current.ensure_document_qa_current(1627)

    assert enqueued == [2544]
    assert status == "enqueued"


async def test_split_envelope_enqueues_only_lost_volume(monkeypatch, fake_redis):
    """同形态第二实体: 分卷商务标在途、技术标已丢失, 只投递技术标。"""
    enqueued = _install_currency(
        monkeypatch, [_doc(3001, "commercial"), _doc(3002, "technical")],
    )
    fake_redis.set(document_qa_stage._inflight_key(3001), "1", ex=180)

    status = await ensure_current.ensure_document_qa_current(88)

    assert enqueued == [3002]
    assert status == "enqueued"


def test_inflight_read_without_redis_falls_back_to_backfill(monkeypatch):
    monkeypatch.setattr("app.core.redis.get_sync_redis", lambda: None)

    assert document_qa_stage.document_qa_inflight(2544) is False


# ── 审查侧挂起: 调用方立即返回, 同项目不叠跑 ─────────────────────────────


async def test_detach_returns_while_currency_backfill_blocks(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_ensure(_project_id):
        started.set()
        await release.wait()
        return "completed"

    monkeypatch.setattr(ensure_current, "ensure_document_qa_current", blocking_ensure)

    first = ensure_current.detach_document_qa_currency(1627)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert first.done() is False
    assert ensure_current.detach_document_qa_currency(1627) is first

    release.set()
    await asyncio.wait_for(first, timeout=1)


# ── 单文档质检: 运行期间在途, 结束(含失败)即撤标 ───────────────────────


async def test_stage_is_inflight_while_running_and_clears_after(monkeypatch, fake_redis):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_gate(_project_id, _document_id):
        return (None, "fh", "ch")

    async def slow_analyze(**_kwargs):
        started.set()
        await release.wait()
        return "completed"

    monkeypatch.setattr(document_qa_stage, "_load_gate", fake_gate)
    monkeypatch.setattr(document_qa_stage, "_analyze_and_persist", slow_analyze)

    run = asyncio.create_task(document_qa_stage.run_document_qa_stage(
        parsed_doc=SimpleNamespace(),
        file_path="bid.pdf",
        project_id=1627,
        document_id=2544,
        heartbeat=lambda *_a, **_k: None,
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    key = document_qa_stage._inflight_key(2544)
    assert document_qa_stage.document_qa_inflight(2544) is True
    assert fake_redis.ttl[key] == document_qa_stage._INFLIGHT_TTL_SEC

    release.set()
    assert await asyncio.wait_for(run, timeout=1) == "completed"
    assert document_qa_stage.document_qa_inflight(2544) is False


async def test_stage_failure_clears_marker_so_next_check_backfills(monkeypatch, fake_redis):
    failed: list[int] = []

    async def fake_gate(_project_id, _document_id):
        return ("technical", "fh", "ch")

    async def crash(**_kwargs):
        raise RuntimeError("版面实例均不可用")

    async def fake_mark_failed(_project_id, document_id, _error):
        failed.append(document_id)

    monkeypatch.setattr(document_qa_stage, "_load_gate", fake_gate)
    monkeypatch.setattr(document_qa_stage, "_analyze_and_persist", crash)
    monkeypatch.setattr(document_qa_stage, "_mark_failed", fake_mark_failed)

    status = await document_qa_stage.run_document_qa_stage(
        parsed_doc=SimpleNamespace(),
        file_path="tech.pdf",
        project_id=88,
        document_id=3002,
        heartbeat=lambda *_a, **_k: None,
    )

    assert status == "failed"
    assert failed == [3002]
    assert document_qa_stage.document_qa_inflight(3002) is False
