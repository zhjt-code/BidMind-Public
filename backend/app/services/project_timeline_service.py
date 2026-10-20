"""项目级日志时间线服务 (阶段三).

融合 4 个数据源:
  1. system_error_logs    -> 错误/警告主线
  2. task_observations    -> 任务级骨架 (start/stage/end)
  3. project_milestone_events -> INFO 级业务里程碑
  4. audit_logs          -> 用户操作 (仅 resource_type='project' 关联)

按时间序合并, 同 incident_id 的错误日志 in-place 标记为可折叠组。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.project import Project
from app.models.project_milestone_event import ProjectMilestoneEvent
from app.models.system_error_log import SystemErrorLog
from app.models.task_observation import TaskObservation, TaskOutcome, TaskPhase
from app.models.user import User

_VALID_LEVELS = {"WARNING", "ERROR", "CRITICAL"}


async def _fetch_project_summary(
    db: AsyncSession, project_id: int
) -> Optional[dict[str, Any]]:
    """返回项目基础信息 (id/name/status/owner_id/owner_username) 或 None"""
    row = (
        await db.execute(
            select(
                Project.id,
                Project.name,
                Project.status,
                Project.project_type,
                Project.owner_id,
                User.username,
            )
            .outerjoin(User, Project.owner_id == User.id)
            .where(Project.id == project_id)
        )
    ).one_or_none()
    if row is None:
        return None
    # Project.status / project_type 是 Enum, str(enum) 会得到 "ProjectStatus.COMPLETED" 这种 repr,
    # 取 .value 让前端拿到 "completed" / "collusion_check" 这种语义化字符串
    raw_status = row[2]
    raw_type = row[3]
    return {
        "id": row[0],
        "name": row[1],
        "status": raw_status.value if hasattr(raw_status, "value") else (str(raw_status) if raw_status is not None else None),
        "project_type": raw_type.value if hasattr(raw_type, "value") else (str(raw_type) if raw_type is not None else None),
        "owner_id": row[4],
        "owner_username": row[5],
    }


async def _fetch_error_logs(
    db: AsyncSession,
    *,
    project_id: int,
    since: Optional[datetime],
    until: Optional[datetime],
    levels: Optional[list[str]],
    task_type: Optional[str],
    keyword: Optional[str],
) -> list[dict[str, Any]]:
    query = select(SystemErrorLog).where(SystemErrorLog.project_id == project_id)
    if since:
        query = query.where(SystemErrorLog.created_at >= since)
    if until:
        query = query.where(SystemErrorLog.created_at <= until)
    if levels:
        valid = [lv for lv in levels if lv in _VALID_LEVELS]
        if valid:
            query = query.where(SystemErrorLog.level.in_(valid))
    if task_type:
        query = query.where(SystemErrorLog.task_type == task_type)
    if keyword:
        query = query.where(SystemErrorLog.message.ilike(f"%{keyword}%"))

    rows = (await db.execute(query)).scalars().all()
    items: list[dict[str, Any]] = []
    for log in rows:
        items.append({
            "kind": "error_log",
            "ts": log.created_at,
            "id": log.id,
            "level": log.level,
            "category": log.category,
            "task_type": log.task_type,
            "module": log.module,
            "message": log.message,
            "incident_id": log.incident_id,
            "chain_seq": log.chain_seq,
            "is_root_cause": log.is_root_cause,
            "exception_type": log.exception_type,
            "origin_module": log.origin_module,
            "origin_function": log.origin_function,
            "origin_line": log.origin_line,
            "resolved": log.resolved,
        })
    return items


async def _fetch_task_observations(
    db: AsyncSession,
    *,
    project_id: int,
    since: Optional[datetime],
    until: Optional[datetime],
    task_type: Optional[str],
) -> list[dict[str, Any]]:
    query = select(TaskObservation).where(TaskObservation.project_id == project_id)
    if since:
        query = query.where(TaskObservation.created_at >= since)
    if until:
        query = query.where(TaskObservation.created_at <= until)
    if task_type:
        query = query.where(TaskObservation.task_type == task_type)

    rows = (await db.execute(query)).scalars().all()
    items: list[dict[str, Any]] = []
    for obs in rows:
        ts = obs.ended_at if obs.phase == TaskPhase.END else obs.started_at
        items.append({
            "kind": "task_observation",
            "ts": ts or obs.created_at,
            "id": obs.id,
            "task_id": obs.task_id,
            "flow": obs.flow.value if hasattr(obs.flow, "value") else str(obs.flow),
            "task_type": obs.task_type,
            "phase": obs.phase.value if hasattr(obs.phase, "value") else str(obs.phase),
            "stage": obs.stage,
            "outcome": obs.outcome.value if hasattr(obs.outcome, "value") else (str(obs.outcome) if obs.outcome else None),
            "duration_ms": obs.duration_ms,
            "failure_state": obs.failure_state,
            "last_error": obs.last_error,
            "degraded": bool(obs.degraded),
        })
    return items


async def _fetch_milestones(
    db: AsyncSession,
    *,
    project_id: int,
    since: Optional[datetime],
    until: Optional[datetime],
    task_type: Optional[str],
    keyword: Optional[str],
) -> list[dict[str, Any]]:
    query = select(ProjectMilestoneEvent).where(
        ProjectMilestoneEvent.project_id == project_id
    )
    if since:
        query = query.where(ProjectMilestoneEvent.occurred_at >= since)
    if until:
        query = query.where(ProjectMilestoneEvent.occurred_at <= until)
    if task_type:
        query = query.where(ProjectMilestoneEvent.task_type == task_type)
    if keyword:
        query = query.where(ProjectMilestoneEvent.summary.ilike(f"%{keyword}%"))

    rows = (await db.execute(query)).scalars().all()
    items: list[dict[str, Any]] = []
    for ev in rows:
        items.append({
            "kind": "milestone",
            "ts": ev.occurred_at,
            "id": ev.id,
            "task_type": ev.task_type,
            "event_key": ev.event_key,
            "summary": ev.summary,
            "incident_id": ev.incident_id,
            "payload_json": ev.payload_json,
        })
    return items


async def _fetch_audit_logs(
    db: AsyncSession,
    *,
    project_id: int,
    since: Optional[datetime],
    until: Optional[datetime],
    keyword: Optional[str],
) -> list[dict[str, Any]]:
    """audit_logs 仅当 resource_type='project' AND resource_id=project_id 时纳入,
    避免不同 resource_type 的间接关联导致误纳。"""
    query = select(AuditLog).where(
        and_(
            AuditLog.resource_type == "project",
            AuditLog.resource_id == project_id,
        )
    )
    if since:
        query = query.where(AuditLog.created_at >= since)
    if until:
        query = query.where(AuditLog.created_at <= until)
    if keyword:
        query = query.where(AuditLog.detail.ilike(f"%{keyword}%"))

    rows = (await db.execute(query)).scalars().all()
    items: list[dict[str, Any]] = []
    for au in rows:
        items.append({
            "kind": "audit",
            "ts": au.created_at,
            "id": au.id,
            "user_id": au.user_id,
            "username": au.username,
            "action": au.action,
            "resource_type": au.resource_type,
            "resource_id": au.resource_id,
            "detail": au.detail,
        })
    return items


def _serialize_ts(item: dict[str, Any]) -> dict[str, Any]:
    """把 datetime 字段统一序列化为 ISO 字符串, 不破坏其他字段。"""
    ts = item.get("ts")
    item["ts"] = ts.isoformat() if isinstance(ts, datetime) else ts
    return item


def _build_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    errors = warnings = milestones = obs_starts = obs_ends_success = obs_ends_failure = audits = 0
    duration_ms_total = 0
    incident_ids: set[str] = set()
    for it in items:
        kind = it.get("kind")
        if kind == "error_log":
            level = it.get("level")
            if level == "WARNING":
                warnings += 1
            elif level in ("ERROR", "CRITICAL"):
                errors += 1
            iid = it.get("incident_id")
            if iid:
                incident_ids.add(iid)
        elif kind == "milestone":
            milestones += 1
        elif kind == "task_observation":
            phase = it.get("phase")
            if phase == TaskPhase.START.value:
                obs_starts += 1
            elif phase == TaskPhase.END.value:
                outcome = it.get("outcome")
                if outcome == TaskOutcome.SUCCESS.value:
                    obs_ends_success += 1
                elif outcome and outcome != TaskOutcome.SUCCESS.value:
                    obs_ends_failure += 1
                if it.get("duration_ms"):
                    try:
                        duration_ms_total += int(it["duration_ms"])
                    except (TypeError, ValueError):
                        pass
        elif kind == "audit":
            audits += 1

    return {
        "total_events": len(items),
        "errors": errors,
        "warnings": warnings,
        "milestones": milestones,
        "task_starts": obs_starts,
        "task_completed": obs_ends_success,
        "task_failed": obs_ends_failure,
        "audit_events": audits,
        "incident_count": len(incident_ids),
        "total_task_duration_ms": duration_ms_total,
    }


async def get_project_timeline(
    db: AsyncSession,
    *,
    project_id: int,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    levels: Optional[list[str]] = None,
    task_type: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """返回项目级时间线 (融合 4 数据源, 按时间倒序分页)。

    Args:
        db: 异步 session
        project_id: 项目 ID
        since/until: 时间窗口
        levels: 错误日志级别筛选, 列表形式如 ["ERROR","CRITICAL"]; None=全部
        task_type: 任务类型筛选 (作用于 error_logs/observations/milestones)
        keyword: 关键词模糊匹配 message/summary/detail
        page/page_size: 分页

    Returns:
        ``{"project": {...}, "summary": {...}, "items": [...], "total": int, "page": int, "page_size": int}``
    """
    project = await _fetch_project_summary(db, project_id)
    if project is None:
        return {
            "project": None,
            "summary": _build_summary([]),
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 0,
        }

    # 是否跳过非错误源 (用户只想看 ERROR/CRITICAL 时, 仍保留 milestones 与 task_observations 的失败事件)
    only_errors = bool(levels) and all(lv in ("ERROR", "CRITICAL") for lv in levels)

    error_items = await _fetch_error_logs(
        db, project_id=project_id, since=since, until=until,
        levels=levels, task_type=task_type, keyword=keyword,
    )

    if only_errors:
        # 仅看错误时, 仍保留 task_observation 的失败 end 事件 (帮助看到任务为何停止)
        obs_items_all = await _fetch_task_observations(
            db, project_id=project_id, since=since, until=until, task_type=task_type,
        )
        obs_items = [
            it for it in obs_items_all
            if it["phase"] == TaskPhase.END.value and it.get("outcome") not in (None, TaskOutcome.SUCCESS.value)
        ]
        milestone_items: list[dict[str, Any]] = []
        audit_items: list[dict[str, Any]] = []
    else:
        obs_items = await _fetch_task_observations(
            db, project_id=project_id, since=since, until=until, task_type=task_type,
        )
        milestone_items = await _fetch_milestones(
            db, project_id=project_id, since=since, until=until,
            task_type=task_type, keyword=keyword,
        )
        audit_items = await _fetch_audit_logs(
            db, project_id=project_id, since=since, until=until, keyword=keyword,
        )

    all_items = error_items + obs_items + milestone_items + audit_items
    # 按时间倒序; ts 缺失放最后
    # 关键: 用 `is not None` 配合 reverse=True, 让 (True, ts) 排在 (False, None) 前
    all_items.sort(key=lambda x: (x.get("ts") is not None, x.get("ts")), reverse=True)
    summary = _build_summary(all_items)

    total = len(all_items)
    offset = (page - 1) * page_size
    page_items = all_items[offset: offset + page_size]
    page_items = [_serialize_ts(dict(it)) for it in page_items]

    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "project": project,
        "summary": summary,
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }
