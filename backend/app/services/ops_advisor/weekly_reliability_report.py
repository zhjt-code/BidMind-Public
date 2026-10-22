"""
职责: 按周窗从 llm_attempt_events 聚合用途池/节点可靠性, 并落库周度建议
不做: 不写回 priority、不直接调用调度器、不做前端格式化
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai_settings import PRIORITY_NORMAL, load_ai_settings
from app.models.llm_attempt_event import LlmAttemptEvent as E
from app.models.llm_weekly_advice import LlmWeeklyAdvice
from app.services.ops_advisor.priority_advice_rules import (
    ConfigReliabilityStats,
    build_node_readonly_note,
    build_purpose_advice,
)

_CST = ZoneInfo("Asia/Shanghai")
_STRUCTURED_FAIL = ("invalid_json", "schema_invalid")
_PURPOSES = ("review", "vision", "judge", "extract")


def resolve_week_start(now: Optional[datetime] = None) -> datetime:
    """返回「刚结束的自然周」周一 00:00 Asia/Shanghai（带 tzinfo）。

    周日 24:00 CST = 周一 00:00 CST，此时分析的是上一完整周（周一~周日）。
    """
    current = now or datetime.now(_CST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_CST)
    else:
        current = current.astimezone(_CST)
    # weekday: Mon=0 ... Sun=6
    # 若当前是周一 00:00 附近（cron 触发点），上一完整周的周一 = 今天 - 7 天
    # 否则取本周一，再减 7 天得到上一完整周
    this_monday = (current - timedelta(days=current.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    # cron 在周日 16:00 UTC = 周一 00:00 CST 触发时，current.weekday()==0，
    # this_monday 就是「本周一」，上一完整周 = this_monday - 7 days
    return this_monday - timedelta(days=7)


def week_end(week_start: datetime) -> datetime:
    return week_start + timedelta(days=7)


async def _aggregate_by_purpose_config(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    purpose: str,
) -> list[ConfigReliabilityStats]:
    rows = (
        await db.execute(
            select(
                E.config_id,
                E.model_name,
                func.count(),
                func.sum(case((E.outcome == "valid", 1), else_=0)),
                func.sum(case((E.outcome == "empty", 1), else_=0)),
                func.sum(case((E.outcome.in_(_STRUCTURED_FAIL), 1), else_=0)),
                func.percentile_cont(0.95).within_group(E.latency_ms),
            )
            .where(
                E.occurred_at >= start,
                E.occurred_at < end,
                E.traffic_class == "production",
                E.purpose == purpose,
                E.config_id.isnot(None),
            )
            .group_by(E.config_id, E.model_name)
        )
    ).all()

    settings = load_ai_settings()
    priority_map = settings.get_pool_priority(purpose)  # type: ignore[arg-type]
    result: list[ConfigReliabilityStats] = []
    for row in rows:
        config_id = row[0]
        if not config_id:
            continue
        attempts = int(row[2])
        valid = int(row[3] or 0)
        empty = int(row[4] or 0)
        structured_fail = int(row[5] or 0)
        result.append(
            ConfigReliabilityStats(
                config_id=str(config_id),
                model_name=str(row[1] or ""),
                attempts=attempts,
                valid_rate=round(valid / attempts, 4) if attempts else 0.0,
                empty_rate=round(empty / attempts, 4) if attempts else 0.0,
                structured_fail_rate=(
                    round(structured_fail / attempts, 4) if attempts else 0.0
                ),
                latency_p95_ms=float(row[6]) if row[6] is not None else 0.0,
                current_priority=int(
                    priority_map.get(str(config_id), PRIORITY_NORMAL)
                ),
            )
        )
    return result


async def _aggregate_top_nodes(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    limit: int = 10,
) -> list[tuple[str, list[ConfigReliabilityStats]]]:
    """取调用量最高的若干节点，按 config 拆分。"""
    top_nodes = (
        await db.execute(
            select(E.node_key, func.count().label("n"))
            .where(
                E.occurred_at >= start,
                E.occurred_at < end,
                E.traffic_class == "production",
                E.node_key.isnot(None),
            )
            .group_by(E.node_key)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()

    result: list[tuple[str, list[ConfigReliabilityStats]]] = []
    for node_key, _n in top_nodes:
        if not node_key:
            continue
        rows = (
            await db.execute(
                select(
                    E.config_id,
                    E.model_name,
                    func.count(),
                    func.sum(case((E.outcome == "valid", 1), else_=0)),
                    func.percentile_cont(0.95).within_group(E.latency_ms),
                )
                .where(
                    E.occurred_at >= start,
                    E.occurred_at < end,
                    E.traffic_class == "production",
                    E.node_key == node_key,
                    E.config_id.isnot(None),
                )
                .group_by(E.config_id, E.model_name)
            )
        ).all()
        candidates: list[ConfigReliabilityStats] = []
        for row in rows:
            if not row[0]:
                continue
            attempts = int(row[2])
            valid = int(row[3] or 0)
            candidates.append(
                ConfigReliabilityStats(
                    config_id=str(row[0]),
                    model_name=str(row[1] or ""),
                    attempts=attempts,
                    valid_rate=round(valid / attempts, 4) if attempts else 0.0,
                    latency_p95_ms=float(row[4]) if row[4] is not None else 0.0,
                )
            )
        result.append((str(node_key), candidates))
    return result


async def generate_and_persist_weekly_advice(
    db: AsyncSession,
    *,
    week_start: Optional[datetime] = None,
) -> dict[str, Any]:
    """生成并 upsert 周度建议，返回摘要。"""
    ws = week_start or resolve_week_start()
    if ws.tzinfo is None:
        ws = ws.replace(tzinfo=_CST)
    we = week_end(ws)

    created = 0
    updated = 0
    settings = load_ai_settings()

    for purpose in _PURPOSES:
        candidates = await _aggregate_by_purpose_config(
            db, start=ws, end=we, purpose=purpose,
        )
        current_priority = settings.get_pool_priority(purpose)  # type: ignore[arg-type]
        decision = build_purpose_advice(
            purpose=purpose,
            candidates=candidates,
            current_priority=current_priority,
        )
        existing = (
            await db.execute(
                select(LlmWeeklyAdvice).where(
                    LlmWeeklyAdvice.week_start == ws,
                    LlmWeeklyAdvice.scope == "purpose",
                    LlmWeeklyAdvice.target == purpose,
                )
            )
        ).scalar_one_or_none()

        payload = {
            "advice_action": decision.action,
            "recommended_config_id": decision.recommended_config_id,
            "demote_config_id": decision.demote_config_id,
            "proposed_priority": decision.proposed_priority,
            "evidence_json": decision.evidence,
            "rule_id": decision.rule_id,
        }
        if existing is None:
            db.add(
                LlmWeeklyAdvice(
                    week_start=ws,
                    scope="purpose",
                    target=purpose,
                    status="pending",
                    **payload,
                )
            )
            created += 1
        elif existing.status == "pending":
            for k, v in payload.items():
                setattr(existing, k, v)
            updated += 1

    for node_key, candidates in await _aggregate_top_nodes(db, start=ws, end=we):
        decision = build_node_readonly_note(node_key=node_key, candidates=candidates)
        existing = (
            await db.execute(
                select(LlmWeeklyAdvice).where(
                    LlmWeeklyAdvice.week_start == ws,
                    LlmWeeklyAdvice.scope == "node",
                    LlmWeeklyAdvice.target == node_key,
                )
            )
        ).scalar_one_or_none()
        payload = {
            "advice_action": decision.action,
            "recommended_config_id": decision.recommended_config_id,
            "demote_config_id": None,
            "proposed_priority": None,
            "evidence_json": decision.evidence,
            "rule_id": decision.rule_id,
        }
        if existing is None:
            db.add(
                LlmWeeklyAdvice(
                    week_start=ws,
                    scope="node",
                    target=node_key,
                    status="pending",
                    **payload,
                )
            )
            created += 1
        elif existing.status == "pending":
            for k, v in payload.items():
                setattr(existing, k, v)
            updated += 1

    await db.commit()
    return {
        "week_start": ws.isoformat(),
        "week_end": we.isoformat(),
        "created": created,
        "updated": updated,
    }


async def get_advice_by_id(db: AsyncSession, advice_id: int) -> Optional[LlmWeeklyAdvice]:
    return (
        await db.execute(
            select(LlmWeeklyAdvice).where(LlmWeeklyAdvice.id == advice_id)
        )
    ).scalar_one_or_none()


async def list_weekly_advice(
    db: AsyncSession,
    *,
    week_start: Optional[datetime] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """列出周建议。未指定 week_start 时每个 (scope, target) 只返回最新一条。"""
    q = select(LlmWeeklyAdvice)
    if week_start is not None:
        q = q.where(LlmWeeklyAdvice.week_start == week_start)
    if status:
        q = q.where(LlmWeeklyAdvice.status == status)
    if week_start is None:
        q = q.distinct(LlmWeeklyAdvice.scope, LlmWeeklyAdvice.target).order_by(
            LlmWeeklyAdvice.scope.asc(),
            LlmWeeklyAdvice.target.asc(),
            LlmWeeklyAdvice.week_start.desc(),
            LlmWeeklyAdvice.id.desc(),
        )
    else:
        q = q.order_by(
            LlmWeeklyAdvice.scope.asc(),
            LlmWeeklyAdvice.target.asc(),
        )
    q = q.limit(limit)
    rows = (await db.execute(q)).scalars().all()
    items = [
        {
            "id": r.id,
            "week_start": r.week_start.isoformat() if r.week_start else None,
            "scope": r.scope,
            "target": r.target,
            "advice_action": r.advice_action,
            "recommended_config_id": r.recommended_config_id,
            "demote_config_id": r.demote_config_id,
            "proposed_priority": r.proposed_priority,
            "evidence_json": r.evidence_json or {},
            "rule_id": r.rule_id,
            "status": r.status,
            "applied_snapshot_id": r.applied_snapshot_id,
            "applied_at": r.applied_at.isoformat() if r.applied_at else None,
            "applied_by": r.applied_by,
            "writable": r.scope == "purpose" and r.proposed_priority is not None,
        }
        for r in rows
    ]
    from app.services.ops_advisor.advice_display import enrich_weekly_advice_items

    return await enrich_weekly_advice_items(db, items)
