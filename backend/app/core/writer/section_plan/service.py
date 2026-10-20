"""SectionPlan resolution service.

职责: 复用或生成单章论证计划，并在失败时降级到 legacy free_writing。
不做: 不保存章节正文、不持有数据库事务调用 LLM、不渲染图示。
"""

from __future__ import annotations

from typing import Any
import json

from app.core.ai.prompt_registry import resolve_runtime_params
from app.core.writer.prompts.section_plan import get_section_plan_prompt
from app.core.writer.section_plan.contracts import (
    SectionPlanDraft,
    SectionPlanRequest,
    SectionPlanResolution,
    is_scoring_constraint,
    validate_and_materialize_section_plan,
)


def render_section_plan_for_prompt(plan: dict[str, Any] | None) -> str:
    """Render validated SectionPlan payload into compact prompt text."""

    if not plan:
        return "（章节论证计划不可用，按既有上下文直接生成）"
    lines = [f"章节目标: {plan.get('section_goal', '')}"]
    for item in plan.get("arguments", [])[:8]:
        lines.append(f"- 论点: {item.get('claim', '')}")
    for item in plan.get("measures", [])[:8]:
        lines.append(f"- 措施: {item.get('action', '')}; 验证: {item.get('verification', '')}")
    for item in plan.get("acceptance_criteria", [])[:8]:
        lines.append(
            f"- 验收: {item.get('criterion', '')}; 方法: {item.get('verification_method', '')}"
        )
    # industry_anchors 是 planner 消化行业画像后的唯一结构化产出，
    # 不回渲则该次消化对正文生成无效。
    for item in plan.get("industry_anchors", [])[:6]:
        guidance = str(item.get("guidance", "")).strip()
        if guidance:
            lines.append(f"- 行业表达要点: {guidance}")
    return "\n".join(line for line in lines if line.strip())[:3000]


async def resolve_section_plan(
    request: SectionPlanRequest,
    llm_client: Any,
    repository: Any,
) -> SectionPlanResolution:
    """Resolve a ready SectionPlan or fall back safely."""

    ready = await repository.get_ready_by_fingerprint(
        request.outline_node_id,
        request.input_fingerprint,
    )
    if ready is not None:
        return ready

    try:
        system_msg, user_msg = get_section_plan_prompt(
            node_context_json=json.dumps(
                {
                    "project_id": request.project_id,
                    "outline_node_id": request.outline_node_id,
                    "node_title": request.node_title,
                    "target_word_count": request.target_word_count,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            chapter_profile_text=request.chapter_profile_text or "（无章节画像）",
            industry_profile_text=request.industry_profile_text or "（无行业画像）",
            constraints_json=json.dumps(
                [item.model_dump(mode="json") for item in request.constraints],
                ensure_ascii=False,
                sort_keys=True,
            ),
            scoring_points_json=json.dumps(
                [
                    item.model_dump(mode="json")
                    for item in request.constraints
                    if is_scoring_constraint(item)
                ],
                ensure_ascii=False,
                sort_keys=True,
            ),
            evidence_catalog_json=json.dumps(
                [item.model_dump(mode="json") for item in request.evidence_catalog],
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        temperature, max_tokens = resolve_runtime_params("writing.section_plan", 0.0, 3000)
        raw_plan = await llm_client.chat_with_validation(
            user_message=user_msg,
            response_model=SectionPlanDraft,
            system_message=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if isinstance(raw_plan, SectionPlanDraft):
            draft = raw_plan
        elif isinstance(raw_plan, dict):
            draft = SectionPlanDraft(**raw_plan)
        else:
            draft = SectionPlanDraft.model_validate(raw_plan)
        payload = validate_and_materialize_section_plan(draft, request)
        plan_id = await repository.save_ready_plan(request, payload)
        return SectionPlanResolution(
            mode=(
                "planned_sanitized"
                if payload.get("safety_patches")
                else "planned_new"
            ),
            section_plan_id=plan_id,
            plan=payload,
        )
    except Exception as exc:  # noqa: BLE001 - fail-safe planner boundary
        failure_code = "planner_failed"
        await repository.save_failed_plan(
            request,
            failure_code,
            f"{type(exc).__name__}: {str(exc)[:200]}",
        )
        return SectionPlanResolution(
            mode="legacy_fallback",
            failure_code=failure_code,
        )


__all__ = ["render_section_plan_for_prompt", "resolve_section_plan"]
