"""广播站内信服务

集中管理广播的 CRUD、用户态写入与查询。

关键不变量：
- 拉取式模型：广播主体一行；用户态独立维护
- "已读"与"已关闭"语义解耦：标已读不影响 Banner 显示
- 软删除：deleted_at IS NOT NULL 的广播立即对所有用户不可见
"""

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, and_, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.broadcast import Broadcast, BroadcastUserState, BroadcastLevel


# ============ 时间工具 ============

def _utc_now() -> datetime:
    """统一获取 UTC 时间，避免 naive datetime 带来的比较异常。"""
    return datetime.now(timezone.utc)


def _coerce_level(level_str: str) -> BroadcastLevel:
    """字符串 -> 枚举，失败抛 ValueError 由路由层转 400。"""
    try:
        return BroadcastLevel(level_str)
    except ValueError as e:
        raise ValueError(f"无效的广播级别: {level_str}") from e


# ============ 管理员 CRUD ============

async def create_broadcast(
    db: AsyncSession,
    *,
    level: str,
    title: str,
    content: str,
    created_by: Optional[int],
    ref_url: Optional[str] = None,
    show_banner: bool = True,
    dismissible: bool = True,
    starts_at: Optional[datetime] = None,
    ends_at: Optional[datetime] = None,
) -> Broadcast:
    """创建一条广播。

    starts_at 默认为当前时间（立即生效）。调用方负责事务提交。
    """
    broadcast = Broadcast(
        level=_coerce_level(level),
        title=title,
        content=content,
        ref_url=ref_url,
        show_banner=show_banner,
        dismissible=dismissible,
        starts_at=starts_at or _utc_now(),
        ends_at=ends_at,
        created_by=created_by,
    )
    db.add(broadcast)
    await db.flush()
    logger.info(
        f"广播已创建: id={broadcast.id}, level={broadcast.level.value}, "
        f"creator={created_by}, banner={show_banner}"
    )
    return broadcast


async def update_broadcast(
    db: AsyncSession,
    broadcast: Broadcast,
    *,
    level: Optional[str] = None,
    title: Optional[str] = None,
    content: Optional[str] = None,
    ref_url: Optional[str] = None,
    show_banner: Optional[bool] = None,
    dismissible: Optional[bool] = None,
    starts_at: Optional[datetime] = None,
    ends_at: Optional[datetime] = None,
) -> Broadcast:
    """局部更新广播；时间窗口校验由 Schema 层保证。"""
    if level is not None:
        broadcast.level = _coerce_level(level)
    if title is not None:
        broadcast.title = title
    if content is not None:
        broadcast.content = content
    if ref_url is not None:
        broadcast.ref_url = ref_url
    if show_banner is not None:
        broadcast.show_banner = show_banner
    if dismissible is not None:
        broadcast.dismissible = dismissible
    if starts_at is not None:
        broadcast.starts_at = starts_at
    if ends_at is not None:
        broadcast.ends_at = ends_at

    # 更新后再次校验时间窗
    if broadcast.starts_at and broadcast.ends_at and broadcast.starts_at >= broadcast.ends_at:
        raise ValueError("生效时间必须早于失效时间")

    await db.flush()
    return broadcast


async def soft_delete_broadcast(db: AsyncSession, broadcast: Broadcast) -> None:
    """软删除（撤回）：写入 deleted_at，对所有用户立即不可见。"""
    broadcast.deleted_at = _utc_now()
    await db.flush()
    logger.info(f"广播已撤回: id={broadcast.id}")


async def get_broadcast_by_id(
    db: AsyncSession, broadcast_id: int, *, include_deleted: bool = False,
) -> Optional[Broadcast]:
    """按 ID 获取广播。include_deleted=False 时跳过软删除项。"""
    stmt = select(Broadcast).where(Broadcast.id == broadcast_id)
    if not include_deleted:
        stmt = stmt.where(Broadcast.deleted_at.is_(None))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_broadcasts_admin(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    include_deleted: bool = True,
) -> tuple[Sequence[Broadcast], int]:
    """管理员侧列表：默认包含已撤回、过期项。"""
    base = select(Broadcast)
    if not include_deleted:
        base = base.where(Broadcast.deleted_at.is_(None))

    from sqlalchemy import func
    count_stmt = select(func.count(Broadcast.id))
    if not include_deleted:
        count_stmt = count_stmt.where(Broadcast.deleted_at.is_(None))
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    stmt = base.order_by(Broadcast.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    items = result.scalars().all()
    return items, total


# ============ 用户侧查询 ============

def _active_window_clause(now: datetime):
    """当前生效中的广播过滤条件（未删除 & 在时间窗口内）。"""
    return and_(
        Broadcast.deleted_at.is_(None),
        Broadcast.starts_at <= now,
        or_(Broadcast.ends_at.is_(None), Broadcast.ends_at > now),
    )


async def list_banners_for_user(
    db: AsyncSession, user_id: int, *, max_count: int = 3,
) -> list[tuple[Broadcast, bool]]:
    """获取当前用户应显示的 Banner 候选。

    过滤条件：
    - 在生效时间窗口内
    - 未被软删除
    - show_banner=True
    - 用户未关闭（broadcast_user_states.dismissed_at IS NULL）

    返回 (Broadcast, is_read) 列表，按 level 优先级 + 创建时间倒序排序。
    """
    now = _utc_now()
    stmt = (
        select(Broadcast, BroadcastUserState)
        .outerjoin(
            BroadcastUserState,
            and_(
                BroadcastUserState.broadcast_id == Broadcast.id,
                BroadcastUserState.user_id == user_id,
            ),
        )
        .where(
            _active_window_clause(now),
            Broadcast.show_banner.is_(True),
            or_(
                BroadcastUserState.id.is_(None),
                BroadcastUserState.dismissed_at.is_(None),
            ),
        )
        .order_by(Broadcast.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    # Python 侧按 level 优先级排序（CRITICAL > WARNING > INFO > SUCCESS）
    level_priority = {
        BroadcastLevel.CRITICAL: 0,
        BroadcastLevel.WARNING: 1,
        BroadcastLevel.INFO: 2,
        BroadcastLevel.SUCCESS: 3,
    }
    sorted_rows = sorted(
        rows,
        key=lambda r: (level_priority.get(r[0].level, 99), -r[0].id),
    )

    out: list[tuple[Broadcast, bool]] = []
    for broadcast, state in sorted_rows[:max_count]:
        is_read = bool(state and state.read_at is not None)
        out.append((broadcast, is_read))
    return out


async def list_user_broadcasts_in_inbox(
    db: AsyncSession, user_id: int, *, unread_only: bool,
    page: int, page_size: int,
) -> tuple[list[tuple[Broadcast, bool]], int]:
    """获取通知中心列表中"广播"分支的数据（生效中且未撤回）。

    与 banner 不同：
    - 不过滤 dismissed_at（即使关闭了 Banner 也能在通知中心看到）
    - 不过滤 show_banner（show_banner=False 的纯通知中心广播也包含）

    返回 (Broadcast, is_read) 列表 + 总数。
    """
    from sqlalchemy import func
    now = _utc_now()

    base_filter = _active_window_clause(now)
    join_clause = and_(
        BroadcastUserState.broadcast_id == Broadcast.id,
        BroadcastUserState.user_id == user_id,
    )

    if unread_only:
        # 未读 = 没有 state 行 或 read_at IS NULL
        unread_filter = or_(
            BroadcastUserState.id.is_(None),
            BroadcastUserState.read_at.is_(None),
        )
        stmt = (
            select(Broadcast, BroadcastUserState)
            .outerjoin(BroadcastUserState, join_clause)
            .where(base_filter, unread_filter)
            .order_by(Broadcast.created_at.desc())
        )
        count_stmt = (
            select(func.count(Broadcast.id))
            .outerjoin(BroadcastUserState, join_clause)
            .where(base_filter, unread_filter)
        )
    else:
        stmt = (
            select(Broadcast, BroadcastUserState)
            .outerjoin(BroadcastUserState, join_clause)
            .where(base_filter)
            .order_by(Broadcast.created_at.desc())
        )
        count_stmt = select(func.count(Broadcast.id)).where(base_filter)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)
    result = await db.execute(stmt)
    rows = result.all()

    out: list[tuple[Broadcast, bool]] = [
        (b, bool(s and s.read_at is not None)) for b, s in rows
    ]
    return out, total


async def count_unread_broadcasts(db: AsyncSession, user_id: int) -> int:
    """统计用户当前未读的广播数（仅生效中且未撤回的）。"""
    from sqlalchemy import func
    now = _utc_now()
    join_clause = and_(
        BroadcastUserState.broadcast_id == Broadcast.id,
        BroadcastUserState.user_id == user_id,
    )
    stmt = (
        select(func.count(Broadcast.id))
        .outerjoin(BroadcastUserState, join_clause)
        .where(
            _active_window_clause(now),
            or_(
                BroadcastUserState.id.is_(None),
                BroadcastUserState.read_at.is_(None),
            ),
        )
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


# ============ 用户态写入 ============

async def _get_or_create_state(
    db: AsyncSession, user_id: int, broadcast_id: int,
) -> BroadcastUserState:
    """获取用户对广播的状态行；不存在则创建空状态（read_at/dismissed_at 均为空）。"""
    stmt = select(BroadcastUserState).where(
        BroadcastUserState.user_id == user_id,
        BroadcastUserState.broadcast_id == broadcast_id,
    )
    result = await db.execute(stmt)
    state = result.scalar_one_or_none()
    if state is None:
        state = BroadcastUserState(user_id=user_id, broadcast_id=broadcast_id)
        db.add(state)
        await db.flush()
    return state


async def mark_broadcast_read(
    db: AsyncSession, user_id: int, broadcast_id: int,
) -> BroadcastUserState:
    """通知中心点击/标记已读时调用。"""
    state = await _get_or_create_state(db, user_id, broadcast_id)
    if state.read_at is None:
        state.read_at = _utc_now()
        await db.flush()
    return state


async def dismiss_broadcast(
    db: AsyncSession, user_id: int, broadcast_id: int,
) -> BroadcastUserState:
    """用户在 Banner 上点 X 关闭时调用。

    关闭后，该用户后续不再看到此广播的横幅；但通知中心列表仍可见。
    同时把 read_at 写入（关闭即视为看过），以便未读计数同步下降。
    """
    state = await _get_or_create_state(db, user_id, broadcast_id)
    now = _utc_now()
    if state.dismissed_at is None:
        state.dismissed_at = now
    if state.read_at is None:
        state.read_at = now
    await db.flush()
    return state


async def mark_all_broadcasts_read(db: AsyncSession, user_id: int) -> int:
    """将当前用户所有未读广播标记为已读，返回受影响数量。

    实现策略：先 INSERT 缺失的状态行，再 UPDATE 已存在但 read_at 为空的行。
    用 INSERT ... ON CONFLICT DO NOTHING 简化。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = _utc_now()
    # 1) 找出所有生效中且无状态行的广播，批量 insert
    stmt_missing = (
        select(Broadcast.id)
        .outerjoin(
            BroadcastUserState,
            and_(
                BroadcastUserState.broadcast_id == Broadcast.id,
                BroadcastUserState.user_id == user_id,
            ),
        )
        .where(
            _active_window_clause(now),
            BroadcastUserState.id.is_(None),
        )
    )
    missing_ids = (await db.execute(stmt_missing)).scalars().all()

    inserted = 0
    if missing_ids:
        rows = [
            {"user_id": user_id, "broadcast_id": bid, "read_at": now}
            for bid in missing_ids
        ]
        ins = pg_insert(BroadcastUserState).values(rows)
        ins = ins.on_conflict_do_nothing(constraint="uq_broadcast_user_state")
        result = await db.execute(ins)
        inserted = result.rowcount or 0

    # 2) 更新已存在但未读的状态行
    upd_stmt = (
        update(BroadcastUserState)
        .where(
            BroadcastUserState.user_id == user_id,
            BroadcastUserState.read_at.is_(None),
            BroadcastUserState.broadcast_id.in_(
                select(Broadcast.id).where(_active_window_clause(now))
            ),
        )
        .values(read_at=now)
    )
    upd_result = await db.execute(upd_stmt)
    updated = upd_result.rowcount or 0

    await db.flush()
    return inserted + updated
