"""写标渲染配置与已确认格式约束的一致性检查。"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.writer.clause_extractor import get_clause_extractor
from app.models.writing_clause import WritingClause
from app.models.writing_render_profile import WritingRenderProfile

RENDER_PROFILE_SOURCE_SUB_TYPES = {
    "format_rule",
    "submission_rule",
    "submission_notice",
}


def _parse_source_clause_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    ids: list[int] = []
    for item in data:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _is_profile_stale(
    profile: WritingRenderProfile,
    clauses: list[WritingClause],
) -> bool:
    clause_ids = sorted(int(c.id) for c in clauses if c.id is not None)
    profile_ids = sorted(_parse_source_clause_ids(profile.source_clause_ids))
    if profile_ids != clause_ids:
        return True

    profile_updated_at = getattr(profile, "updated_at", None)
    if not isinstance(profile_updated_at, datetime):
        return True

    latest_clause_updated_at = max(
        (
            updated_at
            for updated_at in (getattr(c, "updated_at", None) for c in clauses)
            if isinstance(updated_at, datetime)
        ),
        default=None,
    )
    if latest_clause_updated_at is None:
        return False
    return profile_updated_at < latest_clause_updated_at


async def ensure_render_profile_current(db: AsyncSession, project_id: int) -> None:
    """渲染前确保 WritingRenderProfile 消费的是最新已确认格式/提交约束。"""
    clauses_result = await db.execute(
        select(WritingClause).where(
            WritingClause.project_id == project_id,
            WritingClause.sub_type.in_(RENDER_PROFILE_SOURCE_SUB_TYPES),
            WritingClause.confirmed == 1,
        ).order_by(WritingClause.sort_order)
    )
    format_rules = list(clauses_result.scalars().all())

    profile_result = await db.execute(
        select(WritingRenderProfile).where(
            WritingRenderProfile.project_id == project_id
        )
    )
    profile = profile_result.scalars().first()

    if not format_rules:
        if profile is not None:
            await db.delete(profile)
            await db.commit()
        return

    if profile is not None and not _is_profile_stale(profile, format_rules):
        return

    extractor = get_clause_extractor(db=db, project_id=project_id)
    await extractor.parse_render_profile(project_id, format_rules)
