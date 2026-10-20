"""业务并发闸门与数据库保护的分层契约测试。

职责: 锁定"业务层并发数"与"数据库连接保护"分属两个独立机制这一契约。
不做: 不测 AIMD 的调节算法（见 test_dispatcher_concurrency）、不测真实数据库连接。

背景:
 曾用两道闸门保护连接池: 会话层的 db_session_scope 许可信号量
 (直接控制同时打开的 short session 数), 以及并发层的
 REVIEW_CONCURRENCY_DB_SAFE_CAP (通过限制协程数间接限制 session 数)。

 实测发现后者取值 8 低于 Dispatcher AIMD 容量 40, 使 AIMD 在 8 以上的
 调节全部失效——业务层只放 8 个请求进来, 32 个槽位永远空闲。而会话层信号量
 已经充分保证了连接安全, 并发层那道闸门既冗余又成了真瓶颈。

 本文件把"谁保护什么"固化为可执行契约: 连接数由会话许可决定, 与协程并发数无关。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.core import database as db_module
from app.core.config import settings


@pytest.fixture(autouse=True)
def _reset_admission_semaphore():
    """每个用例独占一个干净的许可信号量，避免懒加载全局在用例间串味。"""
    db_module._db_session_admission_semaphore = None
    db_module._db_session_admission_limit = 0
    yield
    db_module._db_session_admission_semaphore = None
    db_module._db_session_admission_limit = 0


# ---------------------------------------------------------------------------
# 契约一: 连接数上界由会话许可决定，与业务并发数无关
# ---------------------------------------------------------------------------


async def test_session_count_stays_within_admission_limit_under_high_concurrency(
    monkeypatch,
) -> None:
    """并发协程数远超许可上限时，同时打开的 session 数不得越界。

 这是 方案成立的前提: 只有该不变量成立, 放开并发层闸门才是安全的。
 """
    limit = 12
    monkeypatch.setattr(settings, "DB_SESSION_ADMISSION_LIMIT", limit, raising=False)

    live = 0
    peak = 0

    @asynccontextmanager
    async def _fake_session():
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            yield object()
        finally:
            live -= 1

    monkeypatch.setattr(db_module, "AsyncSessionLocal", _fake_session)

    async def _one_worker() -> None:
        async with db_module.db_session_scope():
            await asyncio.sleep(0.01)

    # 业务并发拉到许可上限的 4 倍以上
    await asyncio.gather(*(_one_worker() for _ in range(limit * 4)))

    assert peak <= limit, (
        f"同时打开的 short session 达到 {peak}，超过许可上限 {limit}；"
        "说明存在绕过 db_session_scope 的并发 DB 访问，放开并发层闸门将不安全"
    )
    assert peak > 1, "并发未真正重叠，用例失去意义"


def test_admission_status_reports_configured_limit(monkeypatch) -> None:
    """许可状态必须如实反映实际信号量容量，否则运维无法判断闸门是否生效。"""
    monkeypatch.setattr(settings, "DB_SESSION_ADMISSION_LIMIT", 9, raising=False)
    db_module._ensure_session_admission_semaphore()

    status = db_module.db_session_admission_status()
    assert status["lazy"] is True
    assert status["limit"] == 9
    assert status["in_use"] == 0


# ---------------------------------------------------------------------------
# 契约二: 并发层闸门不得低于 Dispatcher 容量（的直接回归）
# ---------------------------------------------------------------------------


def test_business_concurrency_is_not_capped_below_dispatcher_capacity(
    monkeypatch,
) -> None:
    """多通道时业务并发必须反映 Dispatcher 总容量，而非被 DB cap 压死。

 这是 的核心回归: cap=8 而 Dispatcher 容量 40 时，AIMD 在 8 以上的
 调节全部失效，本用例在修复前必须失败。
 """
    from app.core import config as config_module

    channel_count = 5
    monkeypatch.setattr(config_module, "get_ai_mode", lambda: "cloud", raising=False)

    class _StubDispatcher:
        channel_count = 5

    monkeypatch.setattr(
        "app.core.ai.ai_factory.get_review_dispatcher",
        lambda: _StubDispatcher(),
        raising=False,
    )

    expected = channel_count * settings.REVIEW_CONCURRENCY_CLOUD_MAX
    actual = config_module.get_review_concurrency()

    assert actual == expected, (
        f"业务并发上限为 {actual}，而 Dispatcher 容量为 {expected}；"
        "并发层闸门低于 Dispatcher 容量会让 AIMD 的自适应调节在该值以上完全失效"
    )


def test_db_safe_cap_no_longer_undercuts_dispatcher(monkeypatch) -> None:
    """配置项自身的取值契约: DB 安全上限不得低于单通道 AIMD 上限。

    该值现已退居"协程数绝对上限"，不再承担连接池保护职责；
    若有人把它调回低于 Dispatcher 容量的值，本用例立即失败。
    """
    cap = int(getattr(settings, "REVIEW_CONCURRENCY_DB_SAFE_CAP", 8))
    cloud_max = int(settings.REVIEW_CONCURRENCY_CLOUD_MAX)

    assert cap >= cloud_max * 2, (
        f"REVIEW_CONCURRENCY_DB_SAFE_CAP={cap} 过低（单通道上限 {cloud_max}）；"
        "多通道场景下它会重新成为瓶颈，连接池保护应由 DB_SESSION_ADMISSION_LIMIT 承担"
    )


# ---------------------------------------------------------------------------
# 契约三: 的选值要求（会话许可 + 外层 session < 池容量）
# ---------------------------------------------------------------------------


def test_connection_upper_bound_fits_pool_capacity() -> None:
    """短 session 许可 + 峰值外层 session 必须小于连接池容量。

 这是 注释中写明的选值要求，此前只存在于注释里，
 现固化为可执行断言，防止后人单独调大任一侧而击穿池容量。
 """
    admission = int(settings.DB_SESSION_ADMISSION_LIMIT)
    peak_outer = int(getattr(settings, "WORKER_LLM_MAX_JOBS", 5))
    pool_capacity = int(settings.DB_POOL_SIZE) + int(settings.DB_MAX_OVERFLOW)

    assert admission + peak_outer < pool_capacity, (
        f"会话许可 {admission} + 外层 session 峰值 {peak_outer} "
        f"已达到或超过池容量 {pool_capacity}，QueuePool 存在耗尽风险"
    )
