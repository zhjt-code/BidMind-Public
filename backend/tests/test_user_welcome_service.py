"""UserWelcomeService 单元测试（原子条件更新 + refresh 回填语义）。

使用受控 FakeSession 隔离数据库，明确验证以下三个分支：
1. 首次确认（UPDATE rowcount=1）：提交事务，调用 refresh 从 DB 回填，不手动赋值。
2. 并发第二请求（UPDATE rowcount=0）：不提交，调用 refresh 从 DB 读既有时间，无 SELECT。
3. 已确认用户（快速路径）：零 DB 操作直接返回。

设计意图：
- 测试 1、2 在旧实现（手动赋值、无 refresh）上必然失败；在新 refresh 实现上通过。
- 每个测试都对第一个 execute 语句断言含 `welcome_letter_acknowledged_at IS NULL`，
  锁住原子条件，防止回归去掉 WHERE 子句。
"""

from datetime import datetime, timezone
from typing import Any

import pytest

from app.services.user_welcome_service import UserWelcomeService


# ==================== 测试用辅助对象 ====================


def _make_user(user_id: int, acknowledged_at: datetime | None = None) -> Any:
    """构造最小化 User 替身，仅携带 Service 所需属性。"""
    # 使用普通对象避免 MagicMock 的属性代理语义干扰断言
    class _User:
        pass

    u = _User()
    u.id = user_id
    u.welcome_letter_acknowledged_at = acknowledged_at
    return u


class _FakeExecuteResult:
    """模拟 session.execute() 返回值，可控制 rowcount。"""

    def __init__(self, rowcount: int = 0) -> None:
        self.rowcount = rowcount

    def scalar_one(self) -> Any:
        # 新实现不调用 scalar_one；若旧实现调用则返回 None 使断言失败
        return None


class _FakeSession:
    """最小化 AsyncSession 替身，追踪 execute/commit/refresh 调用。

    约定（对应新实现语义）：
    - 第 1 次 execute 为 UPDATE（IS NULL 原子条件），返回可控 rowcount。
    - rowcount=1 时服务调用 commit()；rowcount=0 时不调用。
    - 两个分支都调用 refresh(user, attribute_names=["welcome_letter_acknowledged_at"])，
      此时将注入的 existing_ts 写回 user，模拟 ORM 从数据库回填属性。
    """

    def __init__(self, update_rowcount: int, existing_ts: datetime | None = None) -> None:
        self._update_rowcount = update_rowcount
        self._existing_ts = existing_ts
        self.committed: bool = False
        self.execute_calls: list = []
        self.refresh_count: int = 0

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _FakeExecuteResult:
        self.execute_calls.append(stmt)
        return _FakeExecuteResult(rowcount=self._update_rowcount)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(
        self,
        obj: Any,
        attribute_names: list[str] | None = None,
        with_for_update: Any = None,
    ) -> None:
        self.refresh_count += 1
        if attribute_names and "welcome_letter_acknowledged_at" in attribute_names:
            obj.welcome_letter_acknowledged_at = self._existing_ts


# ==================== 测试用例 ====================


def _assert_update_has_is_null(session: _FakeSession) -> None:
    """共享断言：第一次 execute 的 SQL 必须含原子条件 `welcome_letter_acknowledged_at IS NULL`。"""
    assert session.execute_calls, "至少应有一次 execute 调用"
    stmt_sql = str(session.execute_calls[0])
    assert "welcome_letter_acknowledged_at IS NULL" in stmt_sql, (
        f"UPDATE 语句必须包含原子 WHERE 条件 `welcome_letter_acknowledged_at IS NULL`，"
        f"实际编译 SQL：\n{stmt_sql}"
    )


@pytest.mark.asyncio
async def test_first_acknowledgement_commits_and_refreshes() -> None:
    """首次确认（UPDATE rowcount=1）：必须提交事务，通过 refresh 从 DB 回填时间，不手动赋值。

    旧实现失败原因：
    - 旧实现不调用 refresh（refresh_count=0），与预期的 1 不符。
    - 旧实现手动赋值 datetime.now()，与 FakeSession 注入的 db_ts 不同。
    """
    db_ts = datetime(2026, 8, 7, 9, 0, 0, tzinfo=timezone.utc)  # 模拟 DB 回填的首次时间
    user = _make_user(user_id=1, acknowledged_at=None)
    session = _FakeSession(update_rowcount=1, existing_ts=db_ts)

    svc = UserWelcomeService(session)
    result = await svc.acknowledge_welcome_letter(user)

    # SQL 原子条件锁定
    _assert_update_has_is_null(session)

    assert result.welcome_letter_acknowledged_at == db_ts, (
        f"结果应为 DB 回填的 db_ts={db_ts}，"
        f"实际={result.welcome_letter_acknowledged_at}"
    )
    assert session.committed, "rowcount=1 必须提交事务"
    assert len(session.execute_calls) == 1, (
        f"rowcount=1 分支只需要 1 次 execute（UPDATE），实际 {len(session.execute_calls)} 次"
    )
    assert session.refresh_count == 1, (
        f"rowcount=1 分支必须调用 refresh 恰好 1 次，实际 {session.refresh_count} 次"
    )


@pytest.mark.asyncio
async def test_concurrent_second_request_refreshes_first_timestamp() -> None:
    """并发第二请求（UPDATE rowcount=0）：不提交，通过 refresh 读首次时间，不发起 SELECT。

    旧实现失败原因：
    - 旧实现发起 SELECT（execute_calls=2），与预期的 1 次不符。
    - 旧实现不调用 refresh（refresh_count=0），与预期的 1 不符。
    """
    first_ts = datetime(2026, 8, 7, 8, 0, 0, tzinfo=timezone.utc)
    user = _make_user(user_id=1, acknowledged_at=None)
    session = _FakeSession(update_rowcount=0, existing_ts=first_ts)

    svc = UserWelcomeService(session)
    result = await svc.acknowledge_welcome_letter(user)

    # SQL 原子条件锁定
    _assert_update_has_is_null(session)

    assert result.welcome_letter_acknowledged_at == first_ts, (
        f"并发第二请求必须通过 refresh 返回数据库首次时间 {first_ts}，"
        f"实际={result.welcome_letter_acknowledged_at}"
    )
    assert not session.committed, "rowcount=0 不应提交事务"
    assert len(session.execute_calls) == 1, (
        f"rowcount=0 分支应只有 1 次 execute（UPDATE），无额外 SELECT，"
        f"实际 {len(session.execute_calls)} 次"
    )
    assert session.refresh_count == 1, (
        f"rowcount=0 分支必须调用 refresh 恰好 1 次（读取既有时间），"
        f"实际 {session.refresh_count} 次"
    )


@pytest.mark.asyncio
async def test_already_acknowledged_user_returns_immediately() -> None:
    """ORM 对象本身已携带非 None 时间戳时，快速路径直接返回，不触发任何 DB 操作。"""
    ts = datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc)
    user = _make_user(user_id=1, acknowledged_at=ts)
    session = _FakeSession(update_rowcount=0, existing_ts=ts)

    svc = UserWelcomeService(session)
    result = await svc.acknowledge_welcome_letter(user)

    assert result.welcome_letter_acknowledged_at == ts
    assert len(session.execute_calls) == 0, "已确认用户不应触发任何 execute"
    assert session.refresh_count == 0, "已确认用户不应触发 refresh"
