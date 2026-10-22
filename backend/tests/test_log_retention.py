"""日志保留清理防御测试 (高危: 自动删数据)。

防御目标:
- 差异化保留: ERROR/CRITICAL 用 error_cutoff(久存), WARNING 用 normal_cutoff(短存)
- 错误链路豁免短 TTL: 处于 normal~error 窗口内的错误不被误删 (覆盖"延后反馈")
- 任务观测按 normal_cutoff 清理

不调用 prune_observability_logs (它会全表删并提交), 只验证只读选择逻辑
`_query_expired_ids`, 用唯一 marker seed + finally 清理, 避免污染库内其他数据。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.system_error_log import SystemErrorLog  # noqa: E402
from app.models.task_observation import TaskObservation  # noqa: E402
from app.services.log_retention_service import (  # noqa: E402
    _ERROR_LEVELS,
    _query_expired_ids,
)


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_each_test():
    """每个 async 测试后释放连接池, 避免 Windows 上跨事件循环的 asyncpg 清理错误。"""
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_error_logs_differentiated_retention() -> None:
    """ERROR 用 30 天线、WARNING 用 7 天线; 10 天前的 ERROR 必须保留。"""
    marker = f"retention-{time.time_ns()}"
    now = datetime.now(timezone.utc)
    error_cutoff = now - timedelta(days=30)
    normal_cutoff = now - timedelta(days=7)

    seeded: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        rows = {
            "err_10d": SystemErrorLog(level="ERROR", message=f"{marker}-err10", source="backend", category="bid_review", created_at=now - timedelta(days=10)),
            "err_40d": SystemErrorLog(level="ERROR", message=f"{marker}-err40", source="backend", category="bid_review", created_at=now - timedelta(days=40)),
            "warn_10d": SystemErrorLog(level="WARNING", message=f"{marker}-warn10", source="backend", category="bid_review", created_at=now - timedelta(days=10)),
            "warn_3d": SystemErrorLog(level="WARNING", message=f"{marker}-warn3", source="backend", category="bid_review", created_at=now - timedelta(days=3)),
        }
        for obj in rows.values():
            db.add(obj)
        await db.flush()
        seeded = {k: v.id for k, v in rows.items()}
        await db.commit()

    try:
        async with AsyncSessionLocal() as db:
            expired_error = set(await _query_expired_ids(
                db, model=SystemErrorLog, ts_column=SystemErrorLog.created_at,
                cutoff=error_cutoff, extra_filter=SystemErrorLog.level.in_(_ERROR_LEVELS),
                limit=100000,
            ))
            expired_warning = set(await _query_expired_ids(
                db, model=SystemErrorLog, ts_column=SystemErrorLog.created_at,
                cutoff=normal_cutoff, extra_filter=SystemErrorLog.level == "WARNING",
                limit=100000,
            ))

        # ERROR 40 天前 -> 删; ERROR 10 天前 -> 保留 (错误久存, 不被 7 天线误删)
        assert seeded["err_40d"] in expired_error
        assert seeded["err_10d"] not in expired_error
        # WARNING 10 天前 -> 删; WARNING 3 天前 -> 保留
        assert seeded["warn_10d"] in expired_warning
        assert seeded["warn_3d"] not in expired_warning
        # 关键交叉验证: 10 天前的 ERROR 不应出现在 WARNING 清理集, 也不被 error 集命中
        assert seeded["err_10d"] not in expired_warning
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                SystemErrorLog.__table__.delete().where(
                    SystemErrorLog.id.in_(list(seeded.values()))
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_task_observations_normal_retention() -> None:
    """任务观测按 7 天线清理: 10 天前删、3 天前留。"""
    marker = f"retention-obs-{time.time_ns()}"
    now = datetime.now(timezone.utc)
    normal_cutoff = now - timedelta(days=7)

    seeded: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        rows = {
            "obs_10d": TaskObservation(task_id=f"{marker}-10", flow="tender", task_type="review", phase="end", created_at=now - timedelta(days=10)),
            "obs_3d": TaskObservation(task_id=f"{marker}-3", flow="tender", task_type="review", phase="end", created_at=now - timedelta(days=3)),
        }
        for obj in rows.values():
            db.add(obj)
        await db.flush()
        seeded = {k: v.id for k, v in rows.items()}
        await db.commit()

    try:
        async with AsyncSessionLocal() as db:
            expired = set(await _query_expired_ids(
                db, model=TaskObservation, ts_column=TaskObservation.created_at,
                cutoff=normal_cutoff, limit=100000,
            ))
        assert seeded["obs_10d"] in expired
        assert seeded["obs_3d"] not in expired
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                TaskObservation.__table__.delete().where(
                    TaskObservation.id.in_(list(seeded.values()))
                )
            )
            await db.commit()
