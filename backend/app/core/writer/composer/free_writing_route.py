"""SectionComposer free_writing 路由子模块（Task 6 Step 7）

承载 free_writing 整套：

- ``compose_free``  自由论述主入口（完整 AI + token 预算 + 图片元信息）
- ``build_context_within_budget``  按优先级分配 token 预算构建上下文
- ``generate_summary``  章节摘要（供后续章节参考）

设计要点：
- 接收 ``composer`` 引用以复用 ``_load_*`` / ``_build_*`` /
  ``_chat_with_input_guard`` / ``llm_client.chat`` 等。
- 与 ``_fill_data`` 一致地通过函数级 import 主模块的 settings /
  _CHARS_PER_TOKEN / _shrink_* / _observe_ai_phrases / get_*_prompt，
  保留 ``monkeypatch.setattr("app.core.writer.section_composer.settings.WRITING_CONTEXT_BUDGET_TOKENS", ...)``
  契约。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from app.core.ai.prompt_registry import resolve_runtime_params
from app.core.writer.composer.clause_directive import build_clause_directives
from app.core.writer.material_consumption_trace import build_consumption_trace
from app.core.writer.image_node_affinity import filter_images_for_node
from app.core.writer.industry_profile_view import (
    PURPOSE_FREE_WRITING,
    build_profile_excerpt,
)
from app.core.writer.numbering import get_sub_numbering_hint, strip_existing_number
from app.core.writer.section_structure import normalize_section_html
from app.core.writer.text_budget import make_priority_shrink_reducer
from app.models.writing_clause import WritingClause
from app.models.writing_material import WritingMaterial
from app.models.writing_material_image import WritingMaterialImage

if TYPE_CHECKING:
    from app.core.writer.section_composer import ComposePayload, SectionComposer
    from app.models.writing_outline_node import WritingOutlineNode


async def await_section_plan_within_budget(
    awaitable: Any,
    *,
    timeout_sec: float,
    request: Any,
    repository: Any,
    node_id: int,
) -> Any:
    """
    职责: 在预算内等待 SectionPlan 解析；超时落 failed 并以带明细的 TimeoutError 退出。
    不做: 不构建请求、不调用 LLM、不物化图示、不生成章节正文。
    """

    started = time.monotonic()
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_sec)
    except TimeoutError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        detail = (
            f"wait_for exceeded {timeout_sec:g}s "
            f"(elapsed_ms={elapsed_ms}, timeout_sec={timeout_sec:g})"
        )
        try:
            await repository.save_failed_plan(
                request,
                "planner_failed",
                f"TimeoutError: {detail}",
            )
        except Exception as persist_exc:
            logger.warning(
                "SectionPlan timeout persist failed: node={}, err={!r}",
                node_id,
                persist_exc,
            )
        raise TimeoutError(detail) from exc


async def compose_free(
    composer: "SectionComposer",
    node: "WritingOutlineNode",
    industry_profile: Optional[str],
    previous_summaries: list[dict[str, str]],
    number_map: Optional[dict[int, str]] = None,
) -> "ComposePayload":
    """自由论述(完整AI + token预算管理 + 图片元信息)"""
    from app.core.writer.section_composer import (
        ComposePayload,
        _observe_ai_phrases,
        _shrink_summary_items,
        get_free_writing_prompt,
        settings,
    )

    chapter_profile = composer._parse_profile(node)
    materials = await composer._load_bound_materials(
        node.id,
        node_title=node.title,
        chapter_profile=chapter_profile,
    )
    clauses = await composer._load_bound_clauses(node.id)
    chunk_samples = await composer._load_material_chunk_samples(
        materials,
        node.title,
        chapter_profile,
    )
    all_images = await composer._load_material_images(materials)
    images = filter_images_for_node(all_images, node.title)

    context = composer._build_context_within_budget(
        industry_profile=industry_profile,
        clauses=clauses,
        materials=materials,
        previous_summaries=previous_summaries,
        images=images,
        chunk_samples=chunk_samples,
        chapter_profile=chapter_profile,
        node_title=node.title,
    )

    chapter_number = (number_map or {}).get(node.id, "")
    sub_hint = get_sub_numbering_hint(node.depth)
    clean_title = strip_existing_number(node.title)
    material_usage_rules = composer._build_material_usage_rules(materials)
    planning_mode = "legacy_flag_off"
    planning_failure_code = None
    section_plan_text = "（未启用章节论证计划，按既有上下文直接生成）"
    section_plan_id = None
    source_clauses = clauses
    diagram_assets: list = []
    diagram_plan_payload: dict[str, Any] | None = None
    try:
        from app.core.writer.section_plan.feature_flags import (
            decide_section_plan_rollout,
            resolve_section_plan_wait_timeout_sec,
        )

        project_id = getattr(composer, "project_id", None)
        decision = decide_section_plan_rollout(project_id)
        planning_mode = decision.reason if not decision.enabled else "planned_new"
        if decision.enabled and composer.db is not None and project_id is not None:
            from app.core.writer.composer.plan_prefetch import build_plan_request_for_node
            from app.core.writer.section_plan.repository import (
                SessionScopedSectionPlanRepository,
            )
            from app.core.writer.section_plan.service import (
                render_section_plan_for_prompt,
                resolve_section_plan,
            )

            built = await build_plan_request_for_node(
                composer,
                project_id=int(project_id),
                node=node,
                industry_profile=industry_profile,
                materials=materials,
                clauses=clauses,
            )
            request = built.request
            planning_clauses = built.planning_clauses
            plan_repo = SessionScopedSectionPlanRepository()
            resolution = await await_section_plan_within_budget(
                resolve_section_plan(
                    request,
                    composer.llm_client,
                    plan_repo,
                ),
                timeout_sec=resolve_section_plan_wait_timeout_sec(),
                request=request,
                repository=plan_repo,
                node_id=node.id,
            )
            planning_mode = resolution.mode
            planning_failure_code = resolution.failure_code
            section_plan_id = resolution.section_plan_id
            section_plan_text = render_section_plan_for_prompt(resolution.plan)
            if resolution.mode in {
                "planned_new",
                "planned_reused",
                "planned_sanitized",
            }:
                source_clauses = planning_clauses
                # P4: 事实型图示物化（单图/整体失败均不降级 SectionPlan）
                try:
                    from app.core.writer.visuals.feature_flags import (
                        decide_section_diagram_rollout,
                    )

                    diagram_decision = decide_section_diagram_rollout(project_id)
                    if (
                        diagram_decision.enabled
                        and resolution.section_plan_id is not None
                        and resolution.plan
                        and resolution.plan.get("visuals")
                    ):
                        from app.core.writer.visuals.eligibility_policy import (
                            filter_visual_intents,
                        )
                        from app.core.writer.visuals.materializer import (
                            materialize_section_diagrams,
                            render_diagram_directives,
                        )
                        from app.core.writer.visuals.repository import (
                            SessionScopedSectionDiagramRepository,
                        )

                        eligibility = filter_visual_intents(
                            resolution.plan,
                            node_title=node.title,
                            chapter_profile=chapter_profile,
                            material_subtypes={
                                str(getattr(image, "business_subtype", "") or "")
                                for image in images
                                if getattr(image, "business_subtype", "")
                            },
                        )
                        diagram_plan_payload = eligibility.payload
                        diagram_repo = SessionScopedSectionDiagramRepository()
                        for rejected, patch in zip(
                            eligibility.rejected, eligibility.patches, strict=True,
                        ):
                            kind = str(patch.get("kind") or "visual_policy_rejected")
                            failure_code = (
                                "diagram_superseded_by_material"
                                if kind == "visual_superseded_by_material"
                                else kind
                            )
                            await diagram_repo.save_failed_diagram(
                                project_id=int(project_id),
                                outline_node_id=node.id,
                                section_plan_id=resolution.section_plan_id,
                                visual_id=str(rejected.get("visual_id", "")),
                                diagram_type=str(rejected.get("diagram_type", "")),
                                title=str(rejected.get("title", "")),
                                failure_code=failure_code,
                            )
                        if diagram_plan_payload.get("visuals"):
                            diagram_assets = await asyncio.wait_for(
                                materialize_section_diagrams(
                                    diagram_repo,
                                    project_id=int(project_id),
                                    outline_node_id=node.id,
                                    section_plan_id=resolution.section_plan_id,
                                    plan_payload=diagram_plan_payload,
                                ),
                                timeout=settings.WRITING_SECTION_DIAGRAM_TIMEOUT_SEC,
                            )
                            directives = render_diagram_directives(diagram_assets)
                            if directives:
                                section_plan_text = f"{section_plan_text}\n\n{directives}"
                except Exception as diagram_exc:
                    logger.warning(
                        "图示物化降级(不影响计划): node={}, err={!r}",
                        node.id, diagram_exc,
                    )
                    diagram_assets = []
    except Exception as exc:
        logger.warning("SectionPlan degraded: node={}, err={!r}", node.id, exc)
        planning_mode = "legacy_fallback"
        planning_failure_code = "planner_failed"
        section_plan_text = "（章节论证计划生成失败，按既有上下文直接生成）"
    logger.info(
        "长文本消费[free_writing]: node={!r}, bound={}, used={}, priority_used={}, priority_segments={}, truncated={}, selected_ids={}",
        node.title,
        context["bound_material_count"],
        len(context["selected_material_ids"]),
        context["priority_materials_used"],
        context["priority_segments_used"],
        context["truncated_materials"],
        context["selected_material_ids"],
    )

    def _build_payload(content: str, *, failure_code: Optional[str] = None) -> "ComposePayload":
        """成功/失败双出口共用的 ComposePayload 构造（除 content 与 failure_code 外字段全同）。"""
        return ComposePayload(
            content=content,
            source_material_ids=context["selected_material_ids"],
            source_clause_ids=composer._dedupe_ids([c.id for c in source_clauses]),
            bound_material_ids=composer._dedupe_ids([m.id for m in materials]),
            selected_material_ids=context["selected_material_ids"],
            consumption_trace=build_consumption_trace(
                bound_material_ids=composer._dedupe_ids([m.id for m in materials]),
                selected_material_ids=context["selected_material_ids"],
                consumed_material_ids=context["selected_material_ids"],
            ),
            planning_mode=planning_mode,
            planning_failure_code=failure_code,
            section_plan_id=section_plan_id,
        )

    try:
        initial_payload: dict[str, object] = {
            "clauses_text": context["clauses_text"],
            "materials_text": context["materials_text"],
            "profile_excerpt": context["profile_excerpt"],
            "chapter_profile_text": context["chapter_profile_text"],
            "previous_summaries": list(previous_summaries),
            "material_usage_rules": material_usage_rules,
        }

        def _render_prompt(payload: dict[str, object]) -> tuple[str, str]:
            summaries_text = "\n".join(
                f"- {item['title']}: {item['summary']}"
                for item in payload["previous_summaries"]  # type: ignore[index]
            )
            return get_free_writing_prompt(
                node_title=clean_title,
                target_word_count=node.target_word_count or 2000,
                clauses_text=str(payload["clauses_text"]),
                materials_text=str(payload["materials_text"]),
                previous_summaries_text=summaries_text,
                industry_profile_excerpt=str(payload["profile_excerpt"]),
                chapter_profile_text=str(payload["chapter_profile_text"]),
                chapter_number=chapter_number,
                sub_numbering_hint=sub_hint,
                material_usage_rules=str(payload["material_usage_rules"]),
                section_plan_text=section_plan_text,
            )

        def _shrink_previous_summaries(reduced: dict[str, object]) -> Optional[str]:
            """前置分支: previous_summaries 超过3条时优先收缩摘要链。"""
            previous_items = list(reduced.get("previous_summaries", []))
            if len(previous_items) > 3:
                reduced["previous_summaries"] = _shrink_summary_items(previous_items)
                return "previous_summaries"
            return None

        _reduce_payload = make_priority_shrink_reducer(
            [
                ("materials_text", 1),
                ("clauses_text", 1),
                ("chapter_profile_text", 1),
                ("profile_excerpt", 1),
            ],
            kept_segments=["node_title", "selected_material_ids"],
            coerce_str=True,
            pre_branch=_shrink_previous_summaries,
        )

        # W1: max_tokens 与建议字数联动，避免长章节因 4000 token 上限烂尾
        from app.core.writer.composer.length_control import (
            compute_section_max_tokens,
            ensure_section_length,
        )

        temperature, registry_max_tokens = resolve_runtime_params(
            "writing.free_writing",
            0.2,
            settings.WRITING_MAX_SECTION_TOKENS,
        )
        max_tokens = compute_section_max_tokens(
            node.target_word_count,
            registry_max_tokens=registry_max_tokens,
        )
        content = await composer._chat_with_input_guard(
            operation_name=f"章节free_writing({node.title})",
            initial_payload=initial_payload,
            render_prompt=_render_prompt,
            reduce_payload_semantically=_reduce_payload,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        normalized = normalize_section_html(content.strip(), clean_title)
        _observe_ai_phrases(normalized)
        # W2: 安全改写（我们→我方 + 段首套话删除），不改事实、不污染 Word
        if settings.WRITING_DE_AI_REWRITE_ENABLED:
            from app.core.writer.de_ai_filter import DeAIFilter
            normalized = DeAIFilter().filter(normalized)
        # W1: 生成后字数闭环（至多一次定向重生成；失败或未改善时保留原稿）
        normalized = await ensure_section_length(
            composer=composer,
            node_id=node.id,
            node_title=clean_title,
            content=normalized,
            target_word_count=node.target_word_count,
        )
        # P4: 图示位置确定性后处理（协议归一 + placement_hint 原位补插 + 无图清洗）
        from app.core.writer.visuals.placement_enforcer import (
            enforce_diagram_placement,
            sanitize_ungrounded_diagram_claims,
        )

        if diagram_assets:
            normalized = enforce_diagram_placement(
                normalized,
                diagram_assets,
                diagram_plan_payload,
            )
        normalized = sanitize_ungrounded_diagram_claims(normalized, diagram_assets)
        return _build_payload(normalized, failure_code=planning_failure_code)
    except Exception as e:
        logger.error(f"free_writing 生成失败: node={node.title}, err={e}", exc_info=True)
        try:
            from app.core.observability.degradation_aggregator import (
                record_degradation as _record_degradation,
            )
            _record_degradation(
                kind="compose_failed",
                ref=f"node_{node.id}:free_writing",
                reason=f"{type(e).__name__}: {str(e)[:120]}",
            )
        except Exception:
            pass
        return _build_payload(
            f"<p>[{node.title} 内容生成失败，请手动补充]</p>",
            failure_code="compose_failed",
        )


def build_context_within_budget(
    composer: "SectionComposer",
    industry_profile: Optional[str],
    clauses: list[WritingClause],
    materials: list[WritingMaterial],
    previous_summaries: list[dict[str, str]],
    images: Optional[list[WritingMaterialImage]] = None,
    chunk_samples: Optional[dict[int, list[str]]] = None,
    chapter_profile: Optional[dict[str, Any]] = None,
    node_title: str = "",
) -> dict[str, str | list[int] | int]:
    """按优先级分配 token 预算构建上下文"""
    from app.core.writer.section_composer import _CHARS_PER_TOKEN, settings

    budget = settings.WRITING_CONTEXT_BUDGET_TOKENS
    used = 0
    chapter_profile_text = composer._format_chapter_profile_text(chapter_profile)

    # P1: 行业画像 (~1500 tokens)。按 free_writing 场景优先级选段，
    # 避免头部截断丢弃排在末节的差异化竞争建议。
    profile_excerpt = ""
    if industry_profile:
        max_chars = min(3000, (budget // 4) * _CHARS_PER_TOKEN)
        profile_excerpt = build_profile_excerpt(
            industry_profile,
            purpose=PURPOSE_FREE_WRITING,
            max_chars=max_chars,
        )
        used += len(profile_excerpt) // _CHARS_PER_TOKEN

    if chapter_profile_text:
        max_chars = min(1500, max((budget - used) // 6, 0) * _CHARS_PER_TOKEN)
        if max_chars > 0:
            chapter_profile_text = chapter_profile_text[:max_chars]
            used += len(chapter_profile_text) // _CHARS_PER_TOKEN

    # P2: 约束 -> 写作指令 (~500-1000 tokens)
    # 读 metadata_json 按写作动作分组(红线/评分点/必写要素/材料/加分),
    # 让提取阶段的 score_rules/deduction_rules/risk_consequence 真正进入生成。
    clause_budget = min(2000, (budget - used) // 3) * _CHARS_PER_TOKEN
    clauses_text = build_clause_directives(clauses, max_chars=clause_budget)
    used += len(clauses_text) // _CHARS_PER_TOKEN

    # P3: 素材 (剩余预算)
    materials_text = ""
    selected_material_ids: list[int] = []
    remaining_chars = (budget - used) * _CHARS_PER_TOKEN
    if remaining_chars > 200:
        material_entries = composer._build_material_context_entries(
            materials,
            chunk_samples=chunk_samples,
            node_title=node_title,
            chapter_profile=chapter_profile,
        )
        has_high_value_text = any(is_priority for _, _, is_priority in material_entries)
        material_budget_ratio = 0.85 if has_high_value_text else 0.7
        mat_parts: list[str] = []
        mc = 0
        material_budget_cap = max(int(remaining_chars * material_budget_ratio), 0)
        priority_materials_used = 0
        priority_segments_used = 0
        truncated_materials = 0
        for material_id, line, _is_priority in material_entries:
            if material_budget_cap <= 0:
                break
            if mc + len(line) > material_budget_cap:
                if not mat_parts:
                    truncated = composer._truncate_material_context_entry(
                        line,
                        material_budget_cap,
                    )
                    if truncated:
                        mat_parts.append(truncated)
                        selected_material_ids.append(material_id)
                        mc += len(truncated)
                        truncated_materials += 1
                        if _is_priority:
                            priority_materials_used += 1
                            priority_segments_used += composer._count_priority_segments_in_entry(truncated)
                else:
                    truncated_materials += 1
                continue
            mat_parts.append(line)
            mc += len(line)
            selected_material_ids.append(material_id)
            if _is_priority:
                priority_materials_used += 1
                priority_segments_used += composer._count_priority_segments_in_entry(line)
        materials_text = "\n\n".join(mat_parts)
        used += mc // _CHARS_PER_TOKEN
    else:
        priority_materials_used = 0
        priority_segments_used = 0
        truncated_materials = 0

    # P3.5: 图片元信息(追加到素材文本)
    if images:
        image_meta = composer._format_image_metadata(images)
        if image_meta:
            materials_text = materials_text + "\n\n" + image_meta

    # P4: 前文摘要 (剩余)
    summaries_parts: list[str] = []
    for s in previous_summaries[-10:]:
        summaries_parts.append(f"- {s['title']}: {s['summary']}")
    summaries_text = "\n".join(summaries_parts)

    return {
        "profile_excerpt": profile_excerpt,
        "chapter_profile_text": chapter_profile_text,
        "clauses_text": clauses_text,
        "materials_text": materials_text,
        "summaries_text": summaries_text,
        "selected_material_ids": selected_material_ids,
        "bound_material_count": len(materials),
        "priority_materials_used": priority_materials_used,
        "priority_segments_used": priority_segments_used,
        "truncated_materials": truncated_materials,
    }


async def generate_summary(
    composer: "SectionComposer", title: str, content: str,
) -> str:
    """生成章节摘要（供后续章节参考）"""
    from app.core.writer.section_composer import get_summarize_prompt

    system_msg, user_msg = get_summarize_prompt(title, content)
    temperature, max_tokens = resolve_runtime_params(
        "writing.section_summary", 0.1, 200,
    )
    try:
        summary = await composer.llm_client.chat(
            user_message=user_msg,
            system_message=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return summary.strip()[:500]
    except Exception as e:
        logger.warning(f"摘要生成失败: {e}")
        return ""
