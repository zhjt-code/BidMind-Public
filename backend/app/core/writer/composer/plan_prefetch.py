"""SectionPlan 预取：在正文生成前并行解析章节论证计划。

职责: 为 free_writing 叶子构建指纹稳定的规划请求，并在阶段1/2期间并行预取。
不做: 不生成章节正文、不物化图示、不改正文 Gate/CoVe/Judge。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class SectionPlanBuildResult:
    """规划请求及其同源约束，供 compose_free 复用 source_clauses。"""

    request: Any
    planning_clauses: list[Any]


def material_entries_from_materials(materials: list[Any]) -> list[dict[str, Any]]:
    """
    职责: 把素材压成 SectionPlan evidence 条目。
    不做: 不截断预算以外的字段、不调用 LLM。
    """
    return [
        {
            "evidence_key": f"material:{m.id}",
            "source_type": "material_excerpt",
            "source_id": m.id,
            "material_id": m.id,
            "excerpt": (getattr(m, "content", None) or getattr(m, "title", "") or "")[:600],
            "fact_eligible": bool(getattr(m, "content", None)),
        }
        for m in materials
        if getattr(m, "id", None) is not None
    ]


async def build_plan_request_for_node(
    composer: Any,
    *,
    project_id: int,
    node: Any,
    industry_profile: str | None,
    materials: list[Any] | None = None,
    clauses: list[Any] | None = None,
) -> SectionPlanBuildResult:
    """
    职责: 为单章构建指纹稳定的 SectionPlanRequest。
    不做: 不调用 LLM、不落库、不消费前文摘要。
    """
    from app.core.writer.prompts.section_plan import get_section_plan_prompt_sha256
    from app.core.writer.section_plan.context import build_section_plan_request
    from app.core.writer.section_plan.source_loader import load_section_plan_clauses

    parse_profile = getattr(composer, "_parse_profile", None)
    chapter_profile = parse_profile(node) if callable(parse_profile) else None
    if materials is None:
        materials = await composer._load_bound_materials(
            node.id,
            node_title=getattr(node, "title", "") or "",
            chapter_profile=chapter_profile,
        )
    if clauses is None:
        clauses = await composer._load_bound_clauses(node.id)

    planning_clauses = await load_section_plan_clauses(
        composer,
        project_id=int(project_id),
        bound_clauses=list(clauses or []),
    )
    request = build_section_plan_request(
        project_id=int(project_id),
        node=node,
        constraints=planning_clauses,
        material_entries=material_entries_from_materials(list(materials or [])),
        industry_profile_text=industry_profile or "",
        prompt_snapshot={
            "planner_prompt_sha256": get_section_plan_prompt_sha256(),
            "source": "code",
        },
    )
    return SectionPlanBuildResult(request=request, planning_clauses=planning_clauses)


async def prefetch_one_section_plan(
    composer: Any,
    *,
    project_id: int,
    node: Any,
    industry_profile: str | None,
) -> Any:
    """
    职责: 预取单章 SectionPlan 并按 ready 指纹落库。
    不做: 不生成正文、不物化图示。
    """
    from app.core.writer.composer.free_writing_route import await_section_plan_within_budget
    from app.core.writer.section_plan.feature_flags import (
        resolve_section_plan_wait_timeout_sec,
    )
    from app.core.writer.section_plan.repository import SessionScopedSectionPlanRepository
    from app.core.writer.section_plan.service import resolve_section_plan

    built = await build_plan_request_for_node(
        composer,
        project_id=int(project_id),
        node=node,
        industry_profile=industry_profile,
    )
    plan_repo = SessionScopedSectionPlanRepository()
    return await await_section_plan_within_budget(
        resolve_section_plan(built.request, composer.llm_client, plan_repo),
        timeout_sec=resolve_section_plan_wait_timeout_sec(),
        request=built.request,
        repository=plan_repo,
        node_id=int(node.id),
    )


def start_section_plan_prefetch(composer: Any, ctx: Any) -> dict[int, asyncio.Task]:
    """
    职责: 按并发上限启动 free_writing 章节计划预取。
    不做: 不 await 完成、不生成正文。
    """
    from app.core.config import settings
    from app.core.writer.section_plan.feature_flags import decide_section_plan_rollout

    if not bool(getattr(settings, "WRITING_PLAN_PREFETCH_ENABLED", False)):
        return {}
    free_nodes = list(getattr(ctx, "free_nodes", None) or [])
    if not free_nodes:
        return {}
    project_id = getattr(composer, "project_id", None)
    if project_id is None or getattr(composer, "db", None) is None:
        return {}
    decision = decide_section_plan_rollout(int(project_id))
    if not decision.enabled:
        return {}

    concurrency = max(1, int(getattr(settings, "WRITING_PLAN_PREFETCH_CONCURRENCY", 8) or 8))
    semaphore = asyncio.Semaphore(concurrency)
    industry_profile = getattr(ctx, "industry_profile", None)

    async def _prefetch_node(node: Any) -> Any:
        async with semaphore:
            return await prefetch_one_section_plan(
                composer,
                project_id=int(project_id),
                node=node,
                industry_profile=industry_profile,
            )

    tasks: dict[int, asyncio.Task] = {}
    for node in free_nodes:
        node_id = getattr(node, "id", None)
        if node_id is None:
            continue
        tasks[int(node_id)] = asyncio.create_task(
            _prefetch_node(node),
            name=f"section-plan-prefetch:{node_id}",
        )
    logger.info(
        "SectionPlan prefetch started: project={}, nodes={}, concurrency={}",
        project_id,
        len(tasks),
        concurrency,
    )
    return tasks


async def await_node_plan_prefetch(
    tasks: dict[int, asyncio.Task] | None,
    node_id: int,
) -> None:
    """
    职责: 等待单章预取结束；失败不阻断正文路径。
    不做: 不重试规划、不生成正文。
    """
    if not tasks:
        return
    task = tasks.get(int(node_id))
    if task is None:
        return
    try:
        await task
    except Exception as exc:
        logger.warning(
            "SectionPlan prefetch degraded: node={}, err={!r}",
            node_id,
            exc,
        )


async def cancel_plan_prefetch(tasks: dict[int, asyncio.Task] | None) -> None:
    """
    职责: 取消尚未完成的预取任务。
    不做: 不改已落库的 ready 计划。
    """
    if not tasks:
        return
    pending = [task for task in tasks.values() if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
