"""运维监控-项目日志聚合与批处理防御测试。

防御目标:
- select_all + exclude 语义: 全选筛选结果后排除指定项目, 目标集正确
- 已删项目按快照可查: project_id 无对应 projects 行时, 聚合用快照名展示 is_deleted
- 批处理写操作 (高危): batch_resolve 标记未处理错误、batch_delete 清理错误日志
- 三表无指向 projects 的外键: 保证删除项目不级联清除可观测数据 (迁移 b9d4e1f6a2c8)

用唯一大号 project_id (不与真实项目 1xxx 冲突) + 唯一 marker seed, finally 清理,
不污染库内真实数据。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.system_error_log import SystemErrorLog  # noqa: E402
from app.services.ops_metrics.project_logs import (  # noqa: E402
    batch_delete_project_logs,
    batch_resolve_project_errors,
    get_flow_project_logs,
    resolve_batch_target_ids,
)


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_each_test():
    yield
    await engine.dispose()


def _unique_pids(count: int) -> list[int]:
    base = 90_000_000 + (time.time_ns() % 8_000_000)
    return [base + i for i in range(count)]


def _mk_log(marker: str, pid: int, *, level: str = "ERROR", resolved: bool = False) -> SystemErrorLog:
    """构造一条"已删项目"(pid 不在 projects 表)的错误日志, 带快照名。"""
    return SystemErrorLog(
        level=level,
        message=f"{marker}-msg-{pid}",
        source="backend",
        category="bid_review",
        project_id=pid,
        project_name=f"{marker}-proj",
        owner_username="ghost-owner",
        resolved=resolved,
        created_at=datetime.now(timezone.utc),
    )


async def _cleanup(pids: list[int]) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            SystemErrorLog.__table__.delete().where(SystemErrorLog.project_id.in_(pids))
        )
        await db.commit()


@pytest.mark.asyncio
async def test_select_all_with_exclude_resolves_correct_targets() -> None:
    """select_all + keyword 命中两个项目, 排除其一后目标集只剩另一个。"""
    marker = f"opslog-{time.time_ns()}"
    pid_a, pid_b = _unique_pids(2)
    async with AsyncSessionLocal() as db:
        db.add(_mk_log(marker, pid_a))
        db.add(_mk_log(marker, pid_b))
        await db.commit()

    try:
        async with AsyncSessionLocal() as db:
            targets = await resolve_batch_target_ids(
                db, flow="tender", select_all=True, project_ids=None,
                exclude_ids=[pid_a], keyword=marker, only_unresolved=False, since=None,
            )
        assert pid_b in targets
        assert pid_a not in targets
    finally:
        await _cleanup([pid_a, pid_b])


@pytest.mark.asyncio
async def test_deleted_project_visible_via_snapshot() -> None:
    """project_id 无对应 projects 行时, 聚合仍按快照名展示且标记 is_deleted。"""
    marker = f"opslog-{time.time_ns()}"
    (pid,) = _unique_pids(1)
    async with AsyncSessionLocal() as db:
        db.add(_mk_log(marker, pid))
        await db.commit()

    try:
        async with AsyncSessionLocal() as db:
            data = await get_flow_project_logs(
                db, flow="tender", page=1, page_size=50, keyword=marker, since=None,
            )
        rows = [it for it in data["items"] if it["project_id"] == pid]
        assert len(rows) == 1, f"应能按快照查到已删项目, 实际 items={data['items']}"
        row = rows[0]
        assert row["is_deleted"] is True
        assert row["project_name"] == f"{marker}-proj"
        assert row["owner_username"] == "ghost-owner"
    finally:
        await _cleanup([pid])


@pytest.mark.asyncio
async def test_batch_resolve_then_delete_project_errors() -> None:
    """批处理写操作: 先把未处理错误标记已处理, 再清理该项目错误日志。"""
    marker = f"opslog-{time.time_ns()}"
    (pid,) = _unique_pids(1)
    async with AsyncSessionLocal() as db:
        db.add(_mk_log(marker, pid, level="ERROR", resolved=False))
        db.add(_mk_log(marker, pid, level="CRITICAL", resolved=False))
        await db.commit()

    try:
        async with AsyncSessionLocal() as db:
            resolve_result = await batch_resolve_project_errors(
                db, flow="tender", project_ids=[pid], resolved_by="test-admin"
            )
            assert resolve_result["resolved"] >= 2
            # 同 session 可见: 该项目错误已全部 resolved
            unresolved = await db.scalar(
                select(func.count()).select_from(SystemErrorLog).where(
                    SystemErrorLog.project_id == pid,
                    SystemErrorLog.resolved.is_(False),
                )
            )
            assert unresolved == 0

            delete_result = await batch_delete_project_logs(
                db, flow="tender", project_ids=[pid]
            )
            assert delete_result["deleted"] >= 2
            await db.commit()

        async with AsyncSessionLocal() as db:
            remaining = await db.scalar(
                select(func.count()).select_from(SystemErrorLog).where(
                    SystemErrorLog.project_id == pid
                )
            )
            assert remaining == 0
    finally:
        await _cleanup([pid])


@pytest.mark.asyncio
async def test_observability_tables_have_no_project_fk() -> None:
    """task_observations / project_milestone_events 不得有指向 projects 的外键,
    否则删除项目会级联清除排障数据 (本功能赖以成立的前提)。"""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(text(
            """
            SELECT conrelid::regclass::text AS tbl
            FROM pg_constraint
            WHERE contype = 'f'
              AND conrelid IN (
                'task_observations'::regclass,
                'project_milestone_events'::regclass
              )
            """
        ))
        fk_tables = [r.tbl for r in rows]
    assert fk_tables == [], f"以下表仍有指向 projects 的外键, 删除会级联: {fk_tables}"
