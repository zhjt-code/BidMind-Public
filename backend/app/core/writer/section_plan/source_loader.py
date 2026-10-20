"""SectionPlan project-level source loader.

职责: 为章节规划补充已确认的项目级背景约束，并与章节绑定约束去重。
不做: 不加载素材、不修改绑定、不把行业画像建议提升为项目事实。
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select

from app.models.writing_clause import WritingClause


def _merge_clauses(
    bound_clauses: Iterable[Any],
    project_context_clauses: Iterable[Any],
) -> list[Any]:
    """保持章节绑定顺序，并追加尚未出现的项目背景约束。"""

    merged: list[Any] = []
    seen: set[int] = set()
    for item in [*bound_clauses, *project_context_clauses]:
        clause_id = getattr(item, "id", None)
        if not isinstance(clause_id, int) or clause_id in seen:
            continue
        seen.add(clause_id)
        merged.append(item)
    return merged


async def load_section_plan_clauses(
    composer: Any,
    *,
    project_id: int,
    bound_clauses: list[Any],
) -> list[Any]:
    """加载已确认 project_context 并合并到 planner 专用约束集合。"""

    if getattr(composer, "db", None) is None:
        return list(bound_clauses)
    cache = getattr(composer, "_compose_cache", None)
    if cache is not None:
        hit, cached = cache.get_project_context(project_id)
        if hit:
            from app.core.writer.composer.compose_metrics import (
                writer_compose_db_roundtrip_total,
            )

            writer_compose_db_roundtrip_total.labels(
                kind="project_context", outcome="hit"
            ).inc()
            return _merge_clauses(bound_clauses, cached or [])
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingClause)
            .where(
                WritingClause.project_id == project_id,
                WritingClause.confirmed == 1,
                WritingClause.sub_type == "project_context",
            )
            .order_by(WritingClause.sort_order, WritingClause.id)
        )
        project_context = list(result.scalars().all())
    if cache is not None:
        cache.set_project_context(project_id, project_context)
    from app.core.writer.composer.compose_metrics import writer_compose_db_roundtrip_total

    writer_compose_db_roundtrip_total.labels(
        kind="project_context", outcome="miss"
    ).inc()
    return _merge_clauses(bound_clauses, project_context)


__all__ = ["load_section_plan_clauses"]
