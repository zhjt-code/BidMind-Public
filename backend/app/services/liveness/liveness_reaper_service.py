"""主动失活巡检 Reaper。

职责: 周期性扫描非终态项目并触发既有失活自愈链。
不做: 自行实现失活判定/恢复逻辑(全部委托既有 ``get_review_status`` /
``compute_collusion_status`` / ``get_project_status`` / ``get_extract_status`` /
``reconcile_document_processing``)。

设计:
- maintenance cron 每分钟触发 ``sweep_stale_projects`` (单 worker 实例 + Redis 单飞锁)
- 仅扫描"任务应在运行"的非终态: PARSING/EXTRACTING/REVIEWING/OUTLINING/MATCHING/WRITING/FORMATTING
- 排除人工等待态(EXTRACTED/OUTLINED/MATCHED/COMPOSED)与终态(CREATED/UPLOADING/COMPLETED/FAILED)
- 仅扫描 ``updated_at`` 早于 ENQUEUE_GRACE_SEC 的项目, 让刚入队的任务获得入队 grace
- 以项目 owner 身份调用现有状态函数, 复用同一套阈值/MAX_RECOVERY/恢复路径
- 每项独立 try/except 隔离, 单项失败不影响后续巡检
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tasks import ENQUEUE_GRACE_SEC
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus, ProjectType
from app.models.user import User


# 仅扫描这些"任务应在运行"的状态. 严格排除人工等待态与终态, 防止误杀。
_RUNNING_STATUSES = (
    ProjectStatus.PARSING,
    ProjectStatus.EXTRACTING,
    ProjectStatus.REVIEWING,
    ProjectStatus.OUTLINING,
    ProjectStatus.MATCHING,
    ProjectStatus.WRITING,
    ProjectStatus.FORMATTING,
)


@dataclass
class SweepStats:
    """单轮巡检统计, 便于日志与监控观察。"""

    scanned: int = 0
    skipped_no_owner: int = 0
    skipped_error: int = 0
    review_reconciled: int = 0
    collusion_reconciled: int = 0
    writing_reconciled: int = 0
    extract_reconciled: int = 0
    docproc_reconciled: int = 0
    docproc_outcomes: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _LockHandle:
    """Redis 单飞锁句柄。"""

    redis_client: object
    key: str
    token: str


_LOCK_KEY = "bidmind:liveness_sweep:lock"


async def _try_acquire_singleflight() -> Optional[_LockHandle]:
    """获取 Redis 单飞锁。返回 None 表示已被并发持有, 当前轮次跳过。

    使用 SET NX EX 原子语义; 失败/Redis 不可用时返回 None, 由调用方跳过本轮以保守起见。
    """
    try:
        from app.core.redis import get_redis_pool
    except Exception:
        return None

    pool = await get_redis_pool()
    if pool is None:
        return None
    token = f"{int(time.time() * 1000)}"
    try:
        # arq.connections.ArqRedis 兼容 redis-py 异步接口
        ttl = int(getattr(settings, "LIVENESS_SWEEP_LOCK_TTL", 55))
        ok = await pool.set(_LOCK_KEY, token, ex=ttl, nx=True)
    except Exception as exc:
        logger.debug("liveness_sweep: 获取单飞锁异常(跳过本轮): {}", exc)
        return None
    if not ok:
        return None
    return _LockHandle(redis_client=pool, key=_LOCK_KEY, token=token)


async def _release_singleflight(handle: Optional[_LockHandle]) -> None:
    """释放单飞锁。仅在 token 仍与本进程持有的 token 一致时删除, 避免误删别人的锁。"""
    if handle is None:
        return
    try:
        current = await handle.redis_client.get(handle.key)  # type: ignore[attr-defined]
        if current is None:
            return
        # arq pool 返回 bytes
        current_str = current.decode() if isinstance(current, (bytes, bytearray)) else str(current)
        if current_str == handle.token:
            await handle.redis_client.delete(handle.key)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug("liveness_sweep: 释放单飞锁异常(忽略): {}", exc)


async def _load_owner(db: AsyncSession, owner_id: Optional[int]) -> Optional[User]:
    """加载 owner User 用于以其身份调用现有状态函数。

    owner_id 为空(历史脏数据)或对应用户已删除时返回 None, 由调用方跳过该项目。
    """
    if owner_id is None:
        return None
    row = await db.execute(select(User).where(User.id == owner_id))
    return row.scalar_one_or_none()


async def _select_stale_projects(
    db: AsyncSession, *, batch_size: int, stale_before: datetime
) -> list[Project]:
    """查询目标状态且超过 grace 的项目, 按 updated_at 升序优先处理最久未推进的。"""
    stmt = (
        select(Project)
        .where(Project.status.in_(_RUNNING_STATUSES))
        .where(Project.updated_at < stale_before)
        .order_by(Project.updated_at.asc())
        .limit(batch_size)
    )
    rows = await db.execute(stmt)
    return list(rows.scalars().all())


async def _select_pending_documents(
    db: AsyncSession, *, project_id: int
) -> list[Document]:
    """该项目下尚未解析完成的文档, 用于触发文档处理 reconciler。"""
    stmt = select(Document).where(
        Document.project_id == project_id,
        Document.parsed != 1,
    )
    rows = await db.execute(stmt)
    return list(rows.scalars().all())


async def _reconcile_review(
    db: AsyncSession, project: Project, owner: User
) -> None:
    """以 owner 身份调用 review 状态函数, 触发既有失活/恢复链。"""
    from app.services.review_service import ReviewService

    service = ReviewService(db)
    await service.get_review_status(project.id, owner)


async def _reconcile_collusion(
    db: AsyncSession, project: Project, owner: User
) -> None:
    """以 owner 身份触发既有 compute_collusion_status 自愈链。"""
    from app.services.collusion_status_service import compute_collusion_status

    await compute_collusion_status(project.id, owner, db)


async def _reconcile_writing(
    db: AsyncSession, project: Project, owner: User
) -> None:
    """以 owner 身份触发既有写标 get_project_status 自愈链。"""
    from app.services.writing_service import WritingService

    service = WritingService(db)
    await service.get_project_status(project.id, owner)


async def _reconcile_project_documents(
    db: AsyncSession, project: Project, stats: SweepStats
) -> None:
    """对项目下所有未完成文档调用共享 reconciler, 处理文档处理任务失活。"""
    from app.services.liveness.document_processing_reconciler import (
        reconcile_document_processing,
    )

    docs = await _select_pending_documents(db, project_id=project.id)
    for doc in docs:
        try:
            result = await reconcile_document_processing(
                db, project_id=project.id, document_id=doc.id
            )
        except Exception as exc:
            logger.warning(
                "liveness_sweep: 文档处理 reconcile 失败 project={} doc={}: {}",
                project.id,
                doc.id,
                exc,
            )
            continue
        if result.outcome != "noop":
            stats.docproc_reconciled += 1
            stats.docproc_outcomes[result.outcome] = (
                stats.docproc_outcomes.get(result.outcome, 0) + 1
            )


async def _reconcile_extract(
    db: AsyncSession, project: Project, owner: User
) -> None:
    """以 owner 身份调用提取状态函数, 触发失活转 FAILED。"""
    from app.services.requirement_service import RequirementService

    service = RequirementService(db)
    await service.get_extract_status(project.id, owner)


async def _dispatch_project(
    db: AsyncSession, project: Project, stats: SweepStats
) -> None:
    """按 project_type + status 分派到对应业务的 reconcile 入口。"""
    owner = await _load_owner(db, project.owner_id)
    if owner is None:
        stats.skipped_no_owner += 1
        logger.debug(
            "liveness_sweep: 跳过 owner 缺失项目 project_id={} owner_id={}",
            project.id,
            project.owner_id,
        )
        return

    project_type = project.project_type
    status = project.status

    # 文档处理对所有项目类型适用; 招标要求提取仅投标审查走 get_extract_status。
    # 写标 EXTRACTING 由 writing get_project_status 负责（同类实例，本次不借机改）。
    if status in (ProjectStatus.PARSING, ProjectStatus.EXTRACTING):
        await _reconcile_project_documents(db, project, stats)
    if status == ProjectStatus.EXTRACTING and project_type == ProjectType.BID_REVIEW:
        await _reconcile_extract(db, project, owner)
        stats.extract_reconciled += 1

    if status == ProjectStatus.REVIEWING:
        if project_type == ProjectType.BID_REVIEW:
            await _reconcile_review(db, project, owner)
            stats.review_reconciled += 1
        elif project_type == ProjectType.COLLUSION_CHECK:
            await _reconcile_collusion(db, project, owner)
            stats.collusion_reconciled += 1
        else:
            # 其他类型走文档处理路径(若有), 不触发业务 reconcile
            pass
    elif status in (
        ProjectStatus.OUTLINING,
        ProjectStatus.MATCHING,
        ProjectStatus.WRITING,
        ProjectStatus.FORMATTING,
    ):
        if project_type == ProjectType.BID_WRITING:
            await _reconcile_writing(db, project, owner)
            stats.writing_reconciled += 1


async def sweep_stale_projects(
    db: AsyncSession,
    *,
    batch_size: Optional[int] = None,
) -> SweepStats:
    """主入口: 扫描非终态项目并逐项触发既有失活自愈。

    单飞锁保护下并发安全; 单项异常隔离, 不影响后续巡检。
    返回 SweepStats 供调用方记录指标/日志。
    """
    stats = SweepStats()

    if not bool(getattr(settings, "LIVENESS_SWEEP_ENABLED", True)):
        logger.debug("liveness_sweep: 已禁用(LIVENESS_SWEEP_ENABLED=false)")
        return stats

    limit = int(batch_size or getattr(settings, "LIVENESS_SWEEP_BATCH", 50))
    grace = ENQUEUE_GRACE_SEC
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=grace)

    lock = await _try_acquire_singleflight()
    if lock is None:
        # 并发已持有或 Redis 不可用, 保守跳过本轮
        logger.debug("liveness_sweep: 单飞锁未获取, 跳过本轮")
        return stats

    try:
        projects = await _select_stale_projects(
            db, batch_size=limit, stale_before=stale_before
        )
        stats.scanned = len(projects)
        if not projects:
            return stats

        logger.info(
            "liveness_sweep: 扫描到 {} 个非终态项目 (grace={}s)",
            len(projects),
            grace,
        )

        for project in projects:
            try:
                await _dispatch_project(db, project, stats)
            except Exception as exc:
                stats.skipped_error += 1
                logger.warning(
                    "liveness_sweep: 项目 {} reconcile 失败(隔离, 继续): {}",
                    project.id,
                    exc,
                )

        logger.info(
            "liveness_sweep 完成: scanned={} review={} collusion={} writing={} "
            "extract={} docproc={} skipped_owner={} skipped_error={} outcomes={}",
            stats.scanned,
            stats.review_reconciled,
            stats.collusion_reconciled,
            stats.writing_reconciled,
            stats.extract_reconciled,
            stats.docproc_reconciled,
            stats.skipped_no_owner,
            stats.skipped_error,
            dict(stats.docproc_outcomes),
        )
    finally:
        await _release_singleflight(lock)

    return stats
