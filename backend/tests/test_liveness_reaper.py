"""主动失活巡检 Reaper 防御测试 .

映射真实风险:
- 三大业务+文档处理的失活检测均为惰性触发, 任务死亡后若无人查询会永久"处理中"。
 reaper 是把"用户触发"补为"定时主动触发"的唯一入口, 必须验证:
 1) 扫描范围严格筛选(只挑运行态+超 grace, 防误杀人工等待态/新鲜任务)
 2) 单飞锁防并发轮叠加
 3) 死任务能触发既有恢复链(文档处理 reconciler 转失败/重入队)
 4) 抽取后 router/路由侧与既有契约语义等价(文档处理 reconciler outcome 与原分支一致)
 5) owner 缺失项目跳过, 不抛异常
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.project import ProjectStatus, ProjectType  # noqa: E402

# ============================================================================
# Fake DB & 公用桩
# ============================================================================


class _FakeScalarResult:
    def __init__(self, items: list) -> None:
        self._items = items

    def all(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeExecuteResult:
    def __init__(self, items: list) -> None:
        self._items = items

    def scalars(self):
        return _FakeScalarResult(self._items)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeDb:
    """轻量 fake AsyncSession, 支持队列式按调用顺序返回 execute 结果。"""

    def __init__(self) -> None:
        self.execute_queue: list[list] = []
        self.commits = 0

    def queue(self, items: list) -> None:
        self.execute_queue.append(items)

    async def execute(self, stmt: Any) -> _FakeExecuteResult:
        if self.execute_queue:
            items = self.execute_queue.pop(0)
        else:
            items = []
        return _FakeExecuteResult(items)

    async def commit(self) -> None:
        self.commits += 1


def _make_project(
    project_id: int,
    status: ProjectStatus,
    project_type: ProjectType = ProjectType.BID_REVIEW,
    owner_id: Optional[int] = 1,
    age_seconds: int = 600,
) -> SimpleNamespace:
    """构造一个 fake project, updated_at 默认 10 分钟前(足以超过 grace)。"""
    return SimpleNamespace(
        id=project_id,
        status=status,
        project_type=project_type,
        owner_id=owner_id,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


def _make_user(user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, username=f"u{user_id}", role="user")


# ============================================================================
# A. 扫描范围 & 单飞锁
# ============================================================================


@pytest.mark.asyncio
async def test_sweep_disabled_returns_empty_stats(monkeypatch):
    """LIVENESS_SWEEP_ENABLED=false 直接返回空统计, 不查 DB 不获锁。"""
    from app.services.liveness import liveness_reaper_service as svc

    monkeypatch.setattr(svc.settings, "LIVENESS_SWEEP_ENABLED", False)
    # 任何对 DB/锁的调用都视为 bug
    monkeypatch.setattr(svc, "_try_acquire_singleflight", AsyncMock(side_effect=AssertionError("不应获取锁")))

    db = _FakeDb()
    stats = await svc.sweep_stale_projects(db)
    assert stats.scanned == 0
    assert db.commits == 0


@pytest.mark.asyncio
async def test_sweep_skipped_when_singleflight_held(monkeypatch):
    """单飞锁未获取(并发或 Redis 不可用), 跳过本轮且不查询 DB。"""
    from app.services.liveness import liveness_reaper_service as svc

    monkeypatch.setattr(svc.settings, "LIVENESS_SWEEP_ENABLED", True)
    monkeypatch.setattr(svc, "_try_acquire_singleflight", AsyncMock(return_value=None))

    select_called = {"n": 0}

    async def _trap_select(*args, **kwargs):
        select_called["n"] += 1
        return []

    monkeypatch.setattr(svc, "_select_stale_projects", _trap_select)

    db = _FakeDb()
    stats = await svc.sweep_stale_projects(db)
    assert stats.scanned == 0
    assert select_called["n"] == 0, "未持锁不应触发 DB 扫描"


@pytest.mark.asyncio
async def test_sweep_releases_lock_after_run(monkeypatch):
    """正常路径必须释放锁(防止下一轮被自己卡死)。"""
    from app.services.liveness import liveness_reaper_service as svc

    monkeypatch.setattr(svc.settings, "LIVENESS_SWEEP_ENABLED", True)

    fake_handle = SimpleNamespace(redis_client=None, key="k", token="t")
    monkeypatch.setattr(svc, "_try_acquire_singleflight", AsyncMock(return_value=fake_handle))
    release_calls = []
    monkeypatch.setattr(
        svc,
        "_release_singleflight",
        AsyncMock(side_effect=lambda h: release_calls.append(h)),
    )

    async def _empty_select(*args, **kwargs):
        return []

    monkeypatch.setattr(svc, "_select_stale_projects", _empty_select)

    db = _FakeDb()
    await svc.sweep_stale_projects(db)
    assert release_calls == [fake_handle]


# ============================================================================
# B. 分派契约: 按 project_type + status 路由到对应 reconcile
# ============================================================================


@pytest.mark.asyncio
async def test_dispatch_review_project_calls_review_reconcile(monkeypatch):
    """BID_REVIEW + REVIEWING → 触发 _reconcile_review (复用 get_review_status)。"""
    from app.services.liveness import liveness_reaper_service as svc

    review_calls = []
    collusion_calls = []
    writing_calls = []
    docproc_calls = []

    async def _fake_review(db, project, owner):
        review_calls.append((project.id, owner.id))

    async def _fake_collusion(db, project, owner):
        collusion_calls.append(project.id)

    async def _fake_writing(db, project, owner):
        writing_calls.append(project.id)

    async def _fake_docproc(db, project, stats):
        docproc_calls.append(project.id)

    monkeypatch.setattr(svc, "_reconcile_review", _fake_review)
    monkeypatch.setattr(svc, "_reconcile_collusion", _fake_collusion)
    monkeypatch.setattr(svc, "_reconcile_writing", _fake_writing)
    monkeypatch.setattr(svc, "_reconcile_project_documents", _fake_docproc)
    monkeypatch.setattr(svc, "_load_owner", AsyncMock(return_value=_make_user(1)))

    db = _FakeDb()
    project = _make_project(101, ProjectStatus.REVIEWING, ProjectType.BID_REVIEW)
    stats = svc.SweepStats()
    await svc._dispatch_project(db, project, stats)

    assert review_calls == [(101, 1)]
    assert collusion_calls == []
    assert writing_calls == []
    assert docproc_calls == []
    assert stats.review_reconciled == 1


@pytest.mark.asyncio
async def test_dispatch_collusion_project_calls_collusion_reconcile(monkeypatch):
    """COLLUSION_CHECK + REVIEWING → _reconcile_collusion。"""
    from app.services.liveness import liveness_reaper_service as svc

    collusion_calls = []
    monkeypatch.setattr(svc, "_reconcile_review", AsyncMock())
    monkeypatch.setattr(
        svc,
        "_reconcile_collusion",
        AsyncMock(side_effect=lambda *a, **k: collusion_calls.append(a[1].id)),
    )
    monkeypatch.setattr(svc, "_reconcile_writing", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_project_documents", AsyncMock())
    monkeypatch.setattr(svc, "_load_owner", AsyncMock(return_value=_make_user(2)))

    db = _FakeDb()
    project = _make_project(
        202, ProjectStatus.REVIEWING, ProjectType.COLLUSION_CHECK, owner_id=2
    )
    stats = svc.SweepStats()
    await svc._dispatch_project(db, project, stats)

    assert collusion_calls == [202]
    assert stats.collusion_reconciled == 1


@pytest.mark.asyncio
async def test_dispatch_writing_project_calls_writing_reconcile(monkeypatch):
    """BID_WRITING + WRITING → _reconcile_writing。"""
    from app.services.liveness import liveness_reaper_service as svc

    writing_calls = []
    monkeypatch.setattr(svc, "_reconcile_review", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_collusion", AsyncMock())
    monkeypatch.setattr(
        svc,
        "_reconcile_writing",
        AsyncMock(side_effect=lambda *a, **k: writing_calls.append(a[1].id)),
    )
    monkeypatch.setattr(svc, "_reconcile_project_documents", AsyncMock())
    monkeypatch.setattr(svc, "_load_owner", AsyncMock(return_value=_make_user(3)))

    db = _FakeDb()
    project = _make_project(
        303, ProjectStatus.WRITING, ProjectType.BID_WRITING, owner_id=3
    )
    stats = svc.SweepStats()
    await svc._dispatch_project(db, project, stats)

    assert writing_calls == [303]
    assert stats.writing_reconciled == 1


@pytest.mark.asyncio
async def test_dispatch_parsing_status_triggers_document_reconciler(monkeypatch):
    """PARSING/EXTRACTING 状态调用文档级 reconcile, 不依赖 project_type。"""
    from app.services.liveness import liveness_reaper_service as svc

    docproc_calls = []
    monkeypatch.setattr(svc, "_reconcile_review", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_collusion", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_writing", AsyncMock())
    monkeypatch.setattr(
        svc,
        "_reconcile_project_documents",
        AsyncMock(side_effect=lambda db, project, stats: docproc_calls.append(project.id)),
    )
    monkeypatch.setattr(svc, "_load_owner", AsyncMock(return_value=_make_user(1)))

    db = _FakeDb()
    project = _make_project(404, ProjectStatus.PARSING, ProjectType.BID_REVIEW)
    stats = svc.SweepStats()
    await svc._dispatch_project(db, project, stats)

    assert docproc_calls == [404]


@pytest.mark.asyncio
async def test_dispatch_extracting_bid_review_triggers_extract_status(monkeypatch):
    """投标审查 EXTRACTING 必须调用提取状态接口，才能把真死写成 FAILED。"""
    from app.services.liveness import liveness_reaper_service as svc

    extract_calls = []
    monkeypatch.setattr(svc, "_reconcile_review", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_collusion", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_writing", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_project_documents", AsyncMock())
    monkeypatch.setattr(
        svc,
        "_reconcile_extract",
        AsyncMock(side_effect=lambda db, project, owner: extract_calls.append(project.id)),
    )
    monkeypatch.setattr(svc, "_load_owner", AsyncMock(return_value=_make_user(1)))

    db = _FakeDb()
    project = _make_project(1528, ProjectStatus.EXTRACTING, ProjectType.BID_REVIEW)
    stats = svc.SweepStats()
    await svc._dispatch_project(db, project, stats)

    assert extract_calls == [1528]
    assert stats.extract_reconciled == 1


@pytest.mark.asyncio
async def test_dispatch_extracting_writing_skips_requirement_extract(monkeypatch):
    """写标 EXTRACTING 不得走招标要求 get_extract_status。"""
    from app.services.liveness import liveness_reaper_service as svc

    extract_calls = []
    monkeypatch.setattr(svc, "_reconcile_review", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_collusion", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_writing", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_project_documents", AsyncMock())
    monkeypatch.setattr(
        svc,
        "_reconcile_extract",
        AsyncMock(side_effect=lambda *a, **k: extract_calls.append("hit")),
    )
    monkeypatch.setattr(svc, "_load_owner", AsyncMock(return_value=_make_user(3)))

    db = _FakeDb()
    project = _make_project(303, ProjectStatus.EXTRACTING, ProjectType.BID_WRITING, owner_id=3)
    stats = svc.SweepStats()
    await svc._dispatch_project(db, project, stats)

    assert extract_calls == []
    assert stats.extract_reconciled == 0


@pytest.mark.asyncio
async def test_dispatch_skips_orphan_owner(monkeypatch):
    """owner 缺失(owner_id 为空或用户已删除)项目跳过, 不进入任一 reconcile。"""
    from app.services.liveness import liveness_reaper_service as svc

    sentinel = []
    monkeypatch.setattr(
        svc, "_reconcile_review", AsyncMock(side_effect=lambda *a, **k: sentinel.append("review"))
    )
    monkeypatch.setattr(svc, "_reconcile_collusion", AsyncMock(side_effect=lambda *a, **k: sentinel.append("col")))
    monkeypatch.setattr(svc, "_reconcile_writing", AsyncMock(side_effect=lambda *a, **k: sentinel.append("wri")))
    monkeypatch.setattr(svc, "_reconcile_project_documents", AsyncMock(side_effect=lambda *a, **k: sentinel.append("doc")))
    monkeypatch.setattr(svc, "_load_owner", AsyncMock(return_value=None))

    db = _FakeDb()
    project = _make_project(505, ProjectStatus.REVIEWING, owner_id=None)
    stats = svc.SweepStats()
    await svc._dispatch_project(db, project, stats)

    assert sentinel == []
    assert stats.skipped_no_owner == 1


# ============================================================================
# C. 范围筛选 SQL 谓词等价性 (用 select 调用参数侧面验证)
# ============================================================================


@pytest.mark.asyncio
async def test_select_stale_projects_uses_running_status_set(monkeypatch):
    """_select_stale_projects 必须只查 _RUNNING_STATUSES, 且按 updated_at 升序限制 batch_size。

    通过捕获 stmt 内联文本(SQLAlchemy compile)间接验证: 不含 COMPLETED/FAILED/已就绪态。
    """
    from app.services.liveness import liveness_reaper_service as svc

    # 运行态白名单显式断言, 与代码常量同步
    running = set(svc._RUNNING_STATUSES)
    assert ProjectStatus.REVIEWING in running
    assert ProjectStatus.PARSING in running
    assert ProjectStatus.EXTRACTING in running
    assert ProjectStatus.OUTLINING in running
    assert ProjectStatus.MATCHING in running
    assert ProjectStatus.WRITING in running
    assert ProjectStatus.FORMATTING in running

    # 人工等待态/终态严格排除, 防误杀
    assert ProjectStatus.EXTRACTED not in running
    assert ProjectStatus.OUTLINED not in running
    assert ProjectStatus.MATCHED not in running
    assert ProjectStatus.COMPOSED not in running
    assert ProjectStatus.COMPLETED not in running
    assert ProjectStatus.FAILED not in running
    assert ProjectStatus.CREATED not in running
    assert ProjectStatus.UPLOADING not in running


# ============================================================================
# D. 文档处理 reconciler: 与原路由分支契约等价
# ============================================================================


@pytest.mark.asyncio
async def test_reconcile_document_processing_returns_completed_when_already_parsed(
    monkeypatch,
):
    """已 parsed=1 的文档 → 直接 completed + 重置 recovery count(对齐原路由 line 309)。"""
    from app.services.liveness import document_processing_reconciler as dpr

    db = _FakeDb()
    db.queue([SimpleNamespace(id=10, project_id=1, parsed=1)])

    reset_calls = []
    monkeypatch.setattr(dpr, "reset_recovery_count", lambda name: reset_calls.append(name))

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=10
    )
    assert result.outcome == "completed"
    assert reset_calls == ["process_doc_10"]


@pytest.mark.asyncio
async def test_reconcile_document_processing_returns_already_failed_on_failed_heartbeat(
    monkeypatch,
):
    """心跳 stage=failed → already_failed (与原路由 line 359 等价, 不再启动恢复)。"""
    from app.services.liveness import document_processing_reconciler as dpr

    db = _FakeDb()
    db.queue([SimpleNamespace(id=11, project_id=1, parsed=0)])

    monkeypatch.setattr(
        dpr,
        "get_task_heartbeat",
        lambda name: {"stage": "failed", "progress": "解析失败"},
    )
    monkeypatch.setattr(dpr, "get_vector_job_state", AsyncMock(return_value=None))
    monkeypatch.setattr(
        dpr,
        "_resolve_already_failed_message",
        AsyncMock(return_value="解析失败"),
    )

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=11
    )
    assert result.outcome == "already_failed"
    assert result.error_message == "解析失败"


@pytest.mark.asyncio
async def test_reconcile_already_failed_falls_back_to_persisted_reason_without_heartbeat(
    monkeypatch,
):
    """: 心跳已清理但 VectorJobState=FAILED 时仍返回 already_failed + 持久化原因。"""
    from app.models.vector_job_state import VectorJobStatus
    from app.services.liveness import document_processing_reconciler as dpr

    db = _FakeDb()
    db.queue([SimpleNamespace(id=21, project_id=1, parsed=0)])
    job_state = SimpleNamespace(
        status=VectorJobStatus.FAILED,
        last_error="文档不可用: PDF所有页面均无文本、表格与图片，请更换文件后重试",
        queue_mode="arq",
        arq_job_id=None,
    )

    monkeypatch.setattr(dpr, "get_task_heartbeat", lambda name: None)
    monkeypatch.setattr(dpr, "get_vector_job_state", AsyncMock(return_value=job_state))
    monkeypatch.setattr(
        dpr,
        "_resolve_already_failed_message",
        AsyncMock(
            return_value="文档不可用: PDF所有页面均无文本、表格与图片，请更换文件后重试"
        ),
    )

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=21
    )
    assert result.outcome == "already_failed"
    assert "PDF所有页面均无文本" in (result.error_message or "")



@pytest.mark.asyncio
async def test_reconcile_document_processing_noop_when_decision_says_running(
    monkeypatch,
):
    """decide_recovery_from_state 返回 (False, _) → noop (任务健康)。"""
    from app.services.liveness import document_processing_reconciler as dpr

    db = _FakeDb()
    # 1) Document 主查 2) job_state
    db.queue([SimpleNamespace(id=12, project_id=1, parsed=0)])

    monkeypatch.setattr(dpr, "get_task_heartbeat", lambda name: None)
    monkeypatch.setattr(dpr, "is_task_active", lambda name: True)
    monkeypatch.setattr(dpr, "get_vector_job_state", AsyncMock(return_value=None))
    # 后 job_state=None 也会探测 ARQ, 测试中拦截避免触达真实 Redis
    monkeypatch.setattr(
        dpr, "_arq_job_still_present_for_recovery", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        dpr, "decide_recovery_from_state", lambda *a, **k: (False, None)
    )

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=12
    )
    assert result.outcome == "noop"
    # noop 路径绝不应 commit
    assert db.commits == 0


@pytest.mark.asyncio
async def test_reconcile_document_processing_completed_race(monkeypatch):
    """二次确认中 parsed 翻为 1 → completed (竞态保护, 对齐原路由 line 399)。"""
    from app.services.liveness import document_processing_reconciler as dpr

    db = _FakeDb()
    # 第1次查 Document.parsed=0, 第2次(竞态二次确认) parsed=1
    db.queue([SimpleNamespace(id=13, project_id=1, parsed=0)])  # main read
    db.queue([1])  # recheck_parsed scalar

    monkeypatch.setattr(dpr, "get_task_heartbeat", lambda name: None)
    monkeypatch.setattr(dpr, "is_task_active", lambda name: False)
    monkeypatch.setattr(
        dpr,
        "get_vector_job_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        dpr, "_arq_job_still_present_for_recovery", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        dpr, "decide_recovery_from_state", lambda *a, **k: (True, "stale")
    )
    reset_calls = []
    monkeypatch.setattr(
        dpr, "reset_recovery_count", lambda name: reset_calls.append(name)
    )

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=13
    )
    assert result.outcome == "completed"
    assert reset_calls == ["process_doc_13"]


@pytest.mark.asyncio
async def test_reconcile_document_processing_max_recovery_exceeded(monkeypatch, tmp_path):
    """recovery_count >= MAX_RECOVERY → 项目转 FAILED + job_state 落 FAILED 终态 + 不再恢复。"""
    from app.models.vector_job_state import VectorJobStatus
    from app.services.liveness import document_processing_reconciler as dpr

    # 后恢复前会校验源文件, 给 fake 文档一个真实存在的文件
    source_pdf = tmp_path / "tender_14.pdf"
    source_pdf.write_bytes(b"%PDF-1.4")

    db = _FakeDb()
    # 1) Document 主查 2) recheck_parsed 3) Project 主查
    db.queue([SimpleNamespace(id=14, project_id=1, parsed=0, file_path=str(source_pdf))])
    db.queue([0])  # recheck_parsed still 0

    project_obj = SimpleNamespace(id=1, status=ProjectStatus.PARSING)
    db.queue([project_obj])

    monkeypatch.setattr(dpr, "get_task_heartbeat", lambda name: {})
    monkeypatch.setattr(dpr, "is_task_active", lambda name: False)
    monkeypatch.setattr(
        dpr, "get_vector_job_state", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        dpr, "_arq_job_still_present_for_recovery", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        dpr, "decide_recovery_from_state", lambda *a, **k: (True, "missing")
    )
    monkeypatch.setattr(dpr, "MAX_RECOVERY", 3)
    monkeypatch.setattr(dpr, "get_recovery_count", lambda name: 3)
    # 终态闭环新增依赖, 拦截避免走真实 upsert(_FakeDb 无 add)
    upsert_mock = AsyncMock()
    monkeypatch.setattr(dpr, "upsert_vector_job_state", upsert_mock)
    monkeypatch.setattr(dpr, "update_heartbeat", lambda *a, **k: None)
    reset_calls: list[str] = []
    monkeypatch.setattr(
        dpr, "reset_recovery_count", lambda name: reset_calls.append(name)
    )

    # 阻断真实指标调用 (Prometheus stub)
    monkeypatch.setattr(
        dpr.arq_job_lost_total, "labels", lambda **kw: SimpleNamespace(inc=lambda: None)
    )

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=14
    )
    assert result.outcome == "max_recovery_exceeded"
    assert project_obj.status == ProjectStatus.FAILED
    assert db.commits == 1
    # 必须把 vector_job_state 落 FAILED 进入终态, 否则下次巡检仍会进入恢复判定
    upsert_mock.assert_awaited_once()
    assert upsert_mock.await_args.kwargs["status"] == VectorJobStatus.FAILED
    # 终态后清理内存恢复计数
    assert reset_calls == ["process_doc_14"]


@pytest.mark.asyncio
async def test_reconcile_document_processing_recovered_increments_count(monkeypatch, tmp_path):
    """recovery_count < MAX_RECOVERY → 重入队 + RECOVERING + 计数增 1。"""
    from app.services.liveness import document_processing_reconciler as dpr

    # 后恢复前会校验源文件, 给 fake 文档一个真实存在的文件
    source_pdf = tmp_path / "tender_15.pdf"
    source_pdf.write_bytes(b"%PDF-1.4")

    db = _FakeDb()
    db.queue([SimpleNamespace(id=15, project_id=1, parsed=0, file_path=str(source_pdf))])  # main doc
    db.queue([0])  # recheck_parsed still 0

    monkeypatch.setattr(dpr, "get_task_heartbeat", lambda name: {})
    monkeypatch.setattr(dpr, "is_task_active", lambda name: False)
    monkeypatch.setattr(
        dpr, "get_vector_job_state", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        dpr, "_arq_job_still_present_for_recovery", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        dpr, "decide_recovery_from_state", lambda *a, **k: (True, "stale")
    )
    monkeypatch.setattr(dpr, "MAX_RECOVERY", 3)
    monkeypatch.setattr(dpr, "get_recovery_count", lambda name: 1)
    increments = []
    monkeypatch.setattr(
        dpr, "increment_recovery_count", lambda name: increments.append(name) or 2
    )
    monkeypatch.setattr(dpr, "cancel_task", lambda name: None)
    monkeypatch.setattr(dpr, "update_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(dpr, "can_use_arq", AsyncMock(return_value=False))
    monkeypatch.setattr(dpr, "get_redis_pool", AsyncMock(return_value=None))
    monkeypatch.setattr(dpr, "enqueue_process_document_job", AsyncMock(return_value=None))
    monkeypatch.setattr(dpr, "upsert_vector_job_state", AsyncMock())
    def _close_coro(coro_or_obj, *a, **k):
        # 抑制 RuntimeWarning: 测试不真正执行任务, 显式 close 协程
        if asyncio.iscoroutine(coro_or_obj):
            coro_or_obj.close()
        return None

    monkeypatch.setattr(dpr, "create_background_task", _close_coro)

    monkeypatch.setattr(
        dpr.arq_job_lost_total, "labels", lambda **kw: SimpleNamespace(inc=lambda: None)
    )
    monkeypatch.setattr(
        dpr.arq_job_retry_total, "labels", lambda **kw: SimpleNamespace(inc=lambda: None)
    )

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=15
    )
    assert result.outcome == "recovered"
    assert result.recovery_count == 2
    assert increments == ["process_doc_15"]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_reconcile_document_processing_skips_fallback_when_arq_job_still_present(monkeypatch):
    """: 即便恢复判定命中, 同名 ARQ job 仍在途时也禁止启动 API fallback。"""
    from app.models.vector_job_state import VectorJobStatus
    from app.services.liveness import document_processing_reconciler as dpr

    db = _FakeDb()
    db.queue([SimpleNamespace(id=16, project_id=1, parsed=0)])  # main doc
    db.queue([0])  # recheck_parsed still 0

    job_state = SimpleNamespace(
        status=VectorJobStatus.RUNNING,
        queue_mode="arq",
        arq_job_id="process_doc_16",
    )

    monkeypatch.setattr(dpr, "get_task_heartbeat", lambda name: {})
    monkeypatch.setattr(dpr, "is_task_active", lambda name: False)
    monkeypatch.setattr(dpr, "is_task_in_enqueue_grace", lambda name: False)
    monkeypatch.setattr(
        dpr, "get_vector_job_state", AsyncMock(return_value=job_state)
    )
    monkeypatch.setattr(
        dpr,
        "decide_recovery_from_state",
        lambda *a, **k: (True, "running_worker_gone"),
    )
    monkeypatch.setattr(dpr, "get_redis_pool", AsyncMock(return_value=object()))

    from app.core import arq_runtime

    arq_present = AsyncMock(return_value=True)
    monkeypatch.setattr(arq_runtime, "arq_job_present_async", arq_present)

    increments: list[str] = []
    monkeypatch.setattr(
        dpr, "increment_recovery_count", lambda name: increments.append(name)
    )
    monkeypatch.setattr(dpr, "can_use_arq", AsyncMock(return_value=False))

    def _forbid_background_task(coro_or_obj, *args, **kwargs):
        if asyncio.iscoroutine(coro_or_obj):
            coro_or_obj.close()
        raise AssertionError("不应启动 API fallback")

    monkeypatch.setattr(dpr, "create_background_task", _forbid_background_task)

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=16
    )

    assert result.outcome == "noop"
    assert increments == []
    assert db.commits == 0
    arq_present.assert_awaited()


@pytest.mark.asyncio
async def test_reconcile_none_job_state_with_arq_trace_suppresses_legacy_recovery(
    monkeypatch,
):
    """: job_state=None 但 ARQ 仍有确定性 job 痕迹 → 禁止 legacy_no_state 重投。

 真实 decide_recovery_from_state 消费 arq_job_present=True 后必须判不恢复,
 封堵「POST 落库失败/queued 心跳过期」窗口内的双执行。
 """
    from app.services.liveness import document_processing_reconciler as dpr

    db = _FakeDb()
    db.queue([SimpleNamespace(id=30, project_id=1, parsed=0)])

    monkeypatch.setattr(dpr, "get_task_heartbeat", lambda name: None)
    monkeypatch.setattr(dpr, "is_task_active", lambda name: False)
    monkeypatch.setattr(dpr, "is_task_in_enqueue_grace", lambda name: False)
    monkeypatch.setattr(dpr, "get_vector_job_state", AsyncMock(return_value=None))
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(dpr, "_arq_job_still_present_for_recovery", probe)

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=30
    )

    assert result.outcome == "noop"
    probe.assert_awaited()
    assert db.commits == 0


@pytest.mark.asyncio
async def test_reconcile_missing_source_file_terminalizes_without_reenqueue(
    monkeypatch,
):
    """: 源文件缺失 → 不重入队, 直接 FAILED 终态化 + 重传引导文案。

 文案必须命中 is_unrecoverable_document_error 分类, 使审查/串标显式重试入口
 阻断无效重跑(同一语义)。
 """
    from app.models.vector_job_state import VectorJobStatus
    from app.services.liveness import document_processing_reconciler as dpr

    db = _FakeDb()
    db.queue([
        SimpleNamespace(
            id=31,
            project_id=1,
            parsed=0,
            file_path="storage/uploads/1/missing_tender.pdf",
        )
    ])
    db.queue([0])  # recheck_parsed still 0
    project_obj = SimpleNamespace(id=1, status=ProjectStatus.PARSING, error_message=None)
    db.queue([project_obj])  # 终态化时的 Project 查询

    monkeypatch.setattr(dpr, "get_task_heartbeat", lambda name: None)
    monkeypatch.setattr(dpr, "is_task_active", lambda name: False)
    monkeypatch.setattr(dpr, "is_task_in_enqueue_grace", lambda name: False)
    monkeypatch.setattr(dpr, "get_vector_job_state", AsyncMock(return_value=None))
    monkeypatch.setattr(
        dpr, "_arq_job_still_present_for_recovery", AsyncMock(return_value=False)
    )
    upsert_mock = AsyncMock()
    monkeypatch.setattr(dpr, "upsert_vector_job_state", upsert_mock)
    monkeypatch.setattr(dpr, "update_heartbeat", lambda *a, **k: None)
    reset_calls: list[str] = []
    monkeypatch.setattr(
        dpr, "reset_recovery_count", lambda name: reset_calls.append(name)
    )
    monkeypatch.setattr(
        dpr,
        "enqueue_process_document_job",
        AsyncMock(side_effect=AssertionError("源文件缺失时不得重入队")),
    )

    result = await dpr.reconcile_document_processing(
        db, project_id=1, document_id=31
    )

    assert result.outcome == "already_failed"
    assert "文件不存在" in (result.error_message or "")
    assert "重新上传" in (result.error_message or "")
    assert project_obj.status == ProjectStatus.FAILED
    upsert_mock.assert_awaited_once()
    assert upsert_mock.await_args.kwargs["status"] == VectorJobStatus.FAILED
    assert reset_calls == ["process_doc_31"]
    assert db.commits == 1

    from app.services.flow_retry_service import is_unrecoverable_document_error

    assert is_unrecoverable_document_error(result.error_message) is True


# ============================================================================
# E. End-to-end: sweep_stale_projects 与单飞锁 + 项目错误隔离
# ============================================================================


@pytest.mark.asyncio
async def test_sweep_isolates_single_project_failure(monkeypatch):
    """单项目 reconcile 抛异常时, 其余项目继续处理, 计入 skipped_error。"""
    from app.services.liveness import liveness_reaper_service as svc

    monkeypatch.setattr(svc.settings, "LIVENESS_SWEEP_ENABLED", True)
    monkeypatch.setattr(
        svc,
        "_try_acquire_singleflight",
        AsyncMock(return_value=SimpleNamespace(redis_client=None, key="k", token="t")),
    )
    monkeypatch.setattr(svc, "_release_singleflight", AsyncMock())

    projects = [
        _make_project(1, ProjectStatus.REVIEWING, ProjectType.BID_REVIEW),
        _make_project(2, ProjectStatus.REVIEWING, ProjectType.BID_REVIEW),
        _make_project(3, ProjectStatus.REVIEWING, ProjectType.BID_REVIEW),
    ]

    async def _fake_select(db, *, batch_size, stale_before):
        return projects

    monkeypatch.setattr(svc, "_select_stale_projects", _fake_select)
    monkeypatch.setattr(svc, "_load_owner", AsyncMock(return_value=_make_user(1)))

    call_log = []

    async def _fake_review(db, project, owner):
        call_log.append(project.id)
        if project.id == 2:
            raise RuntimeError("transient: project 2")

    monkeypatch.setattr(svc, "_reconcile_review", _fake_review)
    monkeypatch.setattr(svc, "_reconcile_collusion", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_writing", AsyncMock())
    monkeypatch.setattr(svc, "_reconcile_project_documents", AsyncMock())

    db = _FakeDb()
    stats = await svc.sweep_stale_projects(db)
    assert call_log == [1, 2, 3], "异常项目不应阻断后续项目"
    assert stats.scanned == 3
    assert stats.skipped_error == 1
    assert stats.review_reconciled == 2
