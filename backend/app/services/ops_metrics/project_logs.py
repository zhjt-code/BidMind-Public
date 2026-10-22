"""运维监控 - 项目维度日志聚合 (阶段二)。

职责: 按业务 flow 聚合"项目级日志概览"(错误数/警告数/最近活动/最后错误),
      在世与已删项目统一展示, 支持按项目名/属主搜索与分页
不做: 不渲染时间线详情 (用 project_timeline_service)、不做日志写入与清理

设计要点:
- 以 project_id 为行主键; flow 归属用 system_error_logs.category + task_observations.flow
- 在世项目元信息 join projects/users; 已删项目用日志快照列 (project_name/owner_username)
- 与迁移 b9d4e1f6a2c8 配套: 项目删除后 project_id 仍保留, 故可按 id 聚合
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.system_error_log import SystemErrorLog
from app.models.task_observation import TaskObservation
from app.models.user import User

_ERROR_LEVELS = ("ERROR", "CRITICAL")

# flow(运维页路由) -> system_error_logs.category / project_type (同取值)
FLOW_TO_CATEGORY: dict[str, str] = {
    "tender": "bid_review",
    "collusion": "collusion_check",
    "writer": "bid_writing",
}

# flow(运维页路由) -> task_observations.flow 枚举值 (tender/collusion/writer 命名一致)
FLOW_TO_OBS_FLOW: dict[str, str] = {
    "tender": "tender",
    "collusion": "collusion",
    "writer": "writer",
}


def is_valid_flow(flow: str) -> bool:
    """flow 是否为受支持的三业务链路之一。"""
    return flow in FLOW_TO_CATEGORY


async def _fetch_error_stats(
    db: AsyncSession, *, category: str, since: Optional[datetime]
) -> dict[int, dict[str, Any]]:
    """按 project_id 聚合错误日志计数与最近错误时间。"""
    query = (
        select(
            SystemErrorLog.project_id,
            func.count().filter(SystemErrorLog.level.in_(_ERROR_LEVELS)).label("error_count"),
            func.count().filter(SystemErrorLog.level == "WARNING").label("warning_count"),
            func.count()
            .filter(
                SystemErrorLog.level.in_(_ERROR_LEVELS),
                SystemErrorLog.resolved.is_(False),
            )
            .label("unresolved_count"),
            func.max(SystemErrorLog.created_at).label("last_error_ts"),
        )
        .where(
            SystemErrorLog.category == category,
            SystemErrorLog.project_id.isnot(None),
        )
        .group_by(SystemErrorLog.project_id)
    )
    if since is not None:
        query = query.where(SystemErrorLog.created_at >= since)

    rows = (await db.execute(query)).all()
    return {
        r.project_id: {
            "error_count": r.error_count or 0,
            "warning_count": r.warning_count or 0,
            "unresolved_count": r.unresolved_count or 0,
            "last_error_ts": r.last_error_ts,
        }
        for r in rows
    }


async def _fetch_obs_activity(
    db: AsyncSession, *, obs_flow: str, since: Optional[datetime]
) -> dict[int, datetime]:
    """按 project_id 取任务观测最近活动时间。"""
    query = (
        select(
            TaskObservation.project_id,
            func.max(TaskObservation.created_at).label("last_ts"),
        )
        .where(
            TaskObservation.flow == obs_flow,
            TaskObservation.project_id.isnot(None),
        )
        .group_by(TaskObservation.project_id)
    )
    if since is not None:
        query = query.where(TaskObservation.created_at >= since)

    rows = (await db.execute(query)).all()
    return {r.project_id: r.last_ts for r in rows}


async def _fetch_live_meta(
    db: AsyncSession, *, project_ids: list[int], project_type: str
) -> dict[int, dict[str, Any]]:
    """在世项目元信息 (join users 拿属主名), 仅返回 project_type 匹配的。"""
    if not project_ids:
        return {}
    rows = (
        await db.execute(
            select(Project.id, Project.name, Project.status, User.username)
            .outerjoin(User, Project.owner_id == User.id)
            .where(Project.id.in_(project_ids), Project.project_type == project_type)
        )
    ).all()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        status = r.status.value if hasattr(r.status, "value") else (
            str(r.status) if r.status is not None else None
        )
        out[r.id] = {
            "project_name": r.name,
            "owner_username": r.username,
            "status": status,
            "is_deleted": False,
        }
    return out


async def _fetch_deleted_meta(
    db: AsyncSession, *, project_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """已删项目元信息: 从日志快照列取 (error_logs 优先, 回退 task_observations)。"""
    if not project_ids:
        return {}
    out: dict[int, dict[str, Any]] = {}

    err_query = (
        select(
            SystemErrorLog.project_id,
            SystemErrorLog.project_name,
            SystemErrorLog.owner_username,
        )
        .where(
            SystemErrorLog.project_id.in_(project_ids),
            SystemErrorLog.project_name.isnot(None),
        )
        .distinct(SystemErrorLog.project_id)
        .order_by(SystemErrorLog.project_id, SystemErrorLog.created_at.desc())
    )
    for r in (await db.execute(err_query)).all():
        out[r.project_id] = {
            "project_name": r.project_name,
            "owner_username": r.owner_username,
            "status": "deleted",
            "is_deleted": True,
        }

    missing = [pid for pid in project_ids if pid not in out]
    if missing:
        obs_query = (
            select(
                TaskObservation.project_id,
                TaskObservation.project_name,
                TaskObservation.owner_username,
            )
            .where(
                TaskObservation.project_id.in_(missing),
                TaskObservation.project_name.isnot(None),
            )
            .distinct(TaskObservation.project_id)
            .order_by(TaskObservation.project_id, TaskObservation.created_at.desc())
        )
        for r in (await db.execute(obs_query)).all():
            out[r.project_id] = {
                "project_name": r.project_name,
                "owner_username": r.owner_username,
                "status": "deleted",
                "is_deleted": True,
            }

    # 无任何快照 (极少: 删除发生在快照机制上线前) 给占位, 仍可点开按 id 看时间线
    for pid in project_ids:
        if pid not in out:
            out[pid] = {
                "project_name": f"(已删除项目 #{pid})",
                "owner_username": None,
                "status": "deleted",
                "is_deleted": True,
            }
    return out


async def _fetch_last_error_messages(
    db: AsyncSession, *, project_ids: list[int], category: str
) -> dict[int, str]:
    """当前页项目各自的最近一条 ERROR/CRITICAL 摘要 (截断 200 字)。"""
    if not project_ids:
        return {}
    query = (
        select(SystemErrorLog.project_id, SystemErrorLog.message)
        .where(
            SystemErrorLog.project_id.in_(project_ids),
            SystemErrorLog.category == category,
            SystemErrorLog.level.in_(_ERROR_LEVELS),
        )
        .distinct(SystemErrorLog.project_id)
        .order_by(SystemErrorLog.project_id, SystemErrorLog.created_at.desc())
    )
    return {
        r.project_id: (r.message or "")[:200]
        for r in (await db.execute(query)).all()
    }


async def get_flow_project_logs(
    db: AsyncSession,
    *,
    flow: str,
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    since: Optional[datetime] = None,
    only_unresolved: bool = False,
) -> dict[str, Any]:
    """按 flow 返回项目维度日志概览 (分页 + 按项目名/属主搜索)。

    返回 ``{items, total, page, page_size, total_pages}``;
    items 按最近活动倒序, 含在世与已删项目。
    """
    category = FLOW_TO_CATEGORY[flow]
    obs_flow = FLOW_TO_OBS_FLOW[flow]

    err_stats = await _fetch_error_stats(db, category=category, since=since)
    obs_activity = await _fetch_obs_activity(db, obs_flow=obs_flow, since=since)

    candidate_ids = set(err_stats) | set(obs_activity)
    if not candidate_ids:
        return {"items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

    candidate_list = list(candidate_ids)
    live_meta = await _fetch_live_meta(db, project_ids=candidate_list, project_type=category)
    deleted_ids = [pid for pid in candidate_list if pid not in live_meta]
    deleted_meta = await _fetch_deleted_meta(db, project_ids=deleted_ids)

    rows: list[dict[str, Any]] = []
    for pid in candidate_list:
        meta = live_meta.get(pid) or deleted_meta.get(pid)
        if meta is None:
            continue
        stats = err_stats.get(pid, {})
        err_ts = stats.get("last_error_ts")
        obs_ts = obs_activity.get(pid)
        activity_candidates = [t for t in (err_ts, obs_ts) if t is not None]
        last_activity = max(activity_candidates) if activity_candidates else None
        rows.append({
            "project_id": pid,
            "project_name": meta["project_name"],
            "owner_username": meta["owner_username"],
            "status": meta["status"],
            "is_deleted": meta["is_deleted"],
            "error_count": stats.get("error_count", 0),
            "warning_count": stats.get("warning_count", 0),
            "unresolved_count": stats.get("unresolved_count", 0),
            "last_activity_at": last_activity,
        })

    if keyword:
        kw = keyword.strip().lower()
        rows = [
            r for r in rows
            if (r["project_name"] and kw in r["project_name"].lower())
            or (r["owner_username"] and kw in r["owner_username"].lower())
        ]

    if only_unresolved:
        rows = [r for r in rows if r["unresolved_count"] > 0]

    # 最近活动倒序 (None 放末尾)
    rows.sort(
        key=lambda r: (r["last_activity_at"] is not None, r["last_activity_at"]),
        reverse=True,
    )

    total = len(rows)
    offset = (page - 1) * page_size
    page_rows = rows[offset: offset + page_size]

    page_ids = [r["project_id"] for r in page_rows]
    last_errs = await _fetch_last_error_messages(db, project_ids=page_ids, category=category)
    for r in page_rows:
        r["last_error_summary"] = last_errs.get(r["project_id"])
        if r["last_activity_at"] is not None:
            r["last_activity_at"] = r["last_activity_at"].isoformat()

    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": page_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


async def collect_flow_project_ids(
    db: AsyncSession,
    *,
    flow: str,
    since: Optional[datetime],
    keyword: Optional[str] = None,
    only_unresolved: bool = False,
) -> list[int]:
    """返回该 flow 下匹配筛选的全部候选 project_id (用于 select_all 批处理)。"""
    category = FLOW_TO_CATEGORY[flow]
    obs_flow = FLOW_TO_OBS_FLOW[flow]
    err_stats = await _fetch_error_stats(db, category=category, since=since)
    obs_activity = await _fetch_obs_activity(db, obs_flow=obs_flow, since=since)
    candidate = list(set(err_stats) | set(obs_activity))
    if not candidate:
        return []
    if only_unresolved:
        candidate = [
            pid for pid in candidate
            if err_stats.get(pid, {}).get("unresolved_count", 0) > 0
        ]
    if keyword:
        live = await _fetch_live_meta(db, project_ids=candidate, project_type=category)
        deleted_ids = [pid for pid in candidate if pid not in live]
        deleted = await _fetch_deleted_meta(db, project_ids=deleted_ids)
        kw = keyword.strip().lower()
        matched: list[int] = []
        for pid in candidate:
            meta = live.get(pid) or deleted.get(pid) or {}
            name = (meta.get("project_name") or "").lower()
            owner = (meta.get("owner_username") or "").lower()
            if kw in name or kw in owner:
                matched.append(pid)
        candidate = matched
    return candidate


async def resolve_batch_target_ids(
    db: AsyncSession,
    *,
    flow: str,
    select_all: bool,
    project_ids: Optional[list[int]],
    exclude_ids: Optional[list[int]],
    keyword: Optional[str],
    only_unresolved: bool,
    since: Optional[datetime],
) -> list[int]:
    """解析批处理目标 project_id 集合 (select_all + 排除, 或显式列表)。"""
    if select_all:
        ids = await collect_flow_project_ids(
            db, flow=flow, since=since, keyword=keyword, only_unresolved=only_unresolved
        )
        exclude = set(exclude_ids or [])
        return [pid for pid in ids if pid not in exclude]
    return list(project_ids or [])


async def batch_resolve_project_errors(
    db: AsyncSession, *, flow: str, project_ids: list[int], resolved_by: str
) -> dict[str, int]:
    """把选中项目的未处理 ERROR/CRITICAL 标记为已处理 (限该业务分类)。"""
    if not project_ids:
        return {"resolved": 0, "projects": 0}
    category = FLOW_TO_CATEGORY[flow]
    stmt = (
        update(SystemErrorLog)
        .where(
            SystemErrorLog.category == category,
            SystemErrorLog.project_id.in_(project_ids),
            SystemErrorLog.level.in_(_ERROR_LEVELS),
            SystemErrorLog.resolved.is_(False),
        )
        .values(resolved=True, resolved_by=resolved_by, resolved_at=func.now())
    )
    result = await db.execute(stmt)
    return {"resolved": result.rowcount or 0, "projects": len(project_ids)}


async def batch_delete_project_logs(
    db: AsyncSession, *, flow: str, project_ids: list[int]
) -> dict[str, int]:
    """删除选中项目的错误日志 (限该业务分类); 观测/里程碑骨架交由 TTL 任务清理。"""
    if not project_ids:
        return {"deleted": 0, "projects": 0}
    category = FLOW_TO_CATEGORY[flow]
    stmt = delete(SystemErrorLog).where(
        SystemErrorLog.category == category,
        SystemErrorLog.project_id.in_(project_ids),
    )
    result = await db.execute(stmt)
    return {"deleted": result.rowcount or 0, "projects": len(project_ids)}
