"""全文内容生成编排流水线。

职责: 编排三类章节生成及收尾。
不做: 不实现模板、正文 Prompt、绑定策略、质量判定或持久化细节。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger

from app.core.config import settings
from app.core.writer.composer.compose_cache import ComposeTaskCache
from app.core.writer.composer.compose_metrics import (
    planning_mode_label,
    writer_compose_cache_total,
    writer_compose_commit_total,
    writer_compose_db_roundtrip_total,
    writer_compose_judge_total,
    writer_compose_phase_seconds,
    writer_compose_planning_mode_total,
)
from app.core.writer.composer.draft_persistence import DraftWriteRequest
from app.core.writer.composer.judge_batch import (
    WriterJudgeBatch,
    snapshot_from_compose,
)
from app.core.writer.composer.plan_prefetch import (
    await_node_plan_prefetch,
    cancel_plan_prefetch,
    start_section_plan_prefetch,
)
from app.core.writer.composer import skip_policy
from app.core.writer.numbering import compute_chapter_numbers
from app.models.writing_outline_node import OutlineNodeType

_GATE_FLAGS_UNSET = object()


@dataclass
class ComposeSectionsRequest:
    project_id: int
    progress_callback: Optional[Callable[..., None]] = None
    skip_existing_drafts: bool = False


@dataclass
class ComposeRunContext:
    project_id: int
    industry_profile: Any
    number_map: dict[int, str]
    total: int
    skip_nodes: list[Any]
    fixed_nodes: list[Any]
    data_nodes: list[Any]
    free_nodes: list[Any]
    drafts: list[Any] = field(default_factory=list)
    previous_summaries: list[dict[str, str]] = field(default_factory=list)
    skipped: int = 0
    done_count: int = 0
    compose_start: float = 0.0
    progress_callback: Optional[Callable[..., None]] = None
    cache: Any = None
    judge_batch: Any = None
    defer_judges: bool = False
    plan_prefetch_tasks: dict[int, Any] = field(default_factory=dict)


class ComposePipeline:
    """
    职责: 编排三类章节生成及收尾。
    不做: 不实现模板、正文 Prompt、绑定策略、质量判定或持久化细节。
    """

    def __init__(self, composer: Any) -> None:
        self.composer = composer

    async def compose_sections(self, request: ComposeSectionsRequest) -> list[Any]:
        composer = self.composer
        cache = ComposeTaskCache(project_id=request.project_id)
        composer._compose_cache = cache
        ctx: Optional[ComposeRunContext] = None
        try:
            ctx = await self.prepare_compose_context(request, cache)
            ctx.plan_prefetch_tasks = start_section_plan_prefetch(self.composer, ctx)
            await self.compose_fixed_sections(ctx)
            await self.compose_data_sections(ctx)
            await self.compose_free_sections(ctx)
            await self.drain_deferred_judges(ctx)
            return self.finalize_compose(ctx)
        except Exception:
            if ctx is not None:
                await cancel_plan_prefetch(ctx.plan_prefetch_tasks)
            batch = ctx.judge_batch if ctx is not None else None
            if batch is not None:
                await batch.cancel_judges()
            raise
        finally:
            composer._compose_cache = None
            self.flush_cache_metrics(cache)

    async def prepare_compose_context(
        self,
        request: ComposeSectionsRequest,
        cache: ComposeTaskCache,
    ) -> ComposeRunContext:
        composer = self.composer
        project_id = request.project_id
        started = time.time()
        all_nodes = await composer._load_all_outline_nodes(project_id)
        number_map = compute_chapter_numbers(all_nodes)

        parent_ids = {n.parent_id for n in all_nodes if n.parent_id is not None}
        nodes = [n for n in all_nodes if n.id not in parent_ids]
        industry_profile = await composer._load_industry_profile(project_id)
        await composer._ensure_material_pool_size(project_id)

        sorted_nodes = composer._sort_by_reading_order(nodes)
        total = len(sorted_nodes)
        logger.info(f"内容生成启动: project={project_id}, leaves={total}")

        await self.preload_current_drafts(cache, [node.id for node in sorted_nodes])
        skip_nodes, fixed_nodes, data_nodes, free_nodes = await self.classify_leaf_nodes(
            cache,
            sorted_nodes,
            request.skip_existing_drafts,
        )
        previous_summaries, skipped = await self.collect_skip_summaries(cache, skip_nodes)
        writer_compose_phase_seconds.labels(
            stage="classify", route="all", outcome="ok"
        ).observe(time.time() - started)
        return ComposeRunContext(
            project_id=project_id,
            industry_profile=industry_profile,
            number_map=number_map,
            total=total,
            skip_nodes=skip_nodes,
            fixed_nodes=fixed_nodes,
            data_nodes=data_nodes,
            free_nodes=free_nodes,
            previous_summaries=previous_summaries,
            skipped=skipped,
            compose_start=time.time(),
            progress_callback=request.progress_callback,
            cache=cache,
            judge_batch=WriterJudgeBatch(),
            defer_judges=bool(getattr(settings, "WRITING_JUDGE_DEFER_ENABLED", False)),
        )

    async def preload_current_drafts(
        self,
        cache: ComposeTaskCache,
        node_ids: list[int],
    ) -> bool:
        if not node_ids:
            return True
        composer = self.composer
        execute = getattr(getattr(composer, "db", None), "execute", None)
        lock = getattr(composer, "_db_lock", None)
        if execute is None or lock is None:
            return False
        try:
            from sqlalchemy import select

            from app.models.writing_section_draft import WritingSectionDraft

            async with lock:
                result = await composer.db.execute(
                    select(WritingSectionDraft).where(
                        WritingSectionDraft.outline_node_id.in_(node_ids),
                        WritingSectionDraft.is_current == 1,
                    )
                )
                rows = list(result.scalars().all())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("current-draft preload degraded: {}", exc)
            return False
        by_node = {int(row.outline_node_id): row for row in rows}
        cache.preload_current_drafts({nid: by_node.get(nid) for nid in node_ids})
        writer_compose_db_roundtrip_total.labels(
            kind="current_draft", outcome="preload"
        ).inc()
        return True

    async def classify_leaf_nodes(
        self,
        cache: ComposeTaskCache,
        sorted_nodes: list[Any],
        skip_existing_drafts: bool,
    ) -> tuple[list[Any], list[Any], list[Any], list[Any]]:
        skip_nodes: list[Any] = []
        fixed_nodes: list[Any] = []
        data_nodes: list[Any] = []
        free_nodes: list[Any] = []
        for node in sorted_nodes:
            current_draft = await self.read_current_draft(cache, node.id)
            if skip_policy.should_skip_compose_node(
                node,
                current_draft,
                skip_existing_drafts,
            ):
                skip_nodes.append(node)
            elif node.node_type == OutlineNodeType.fixed_template:
                fixed_nodes.append(node)
            elif node.node_type == OutlineNodeType.data_fill:
                data_nodes.append(node)
            else:
                free_nodes.append(node)
        return skip_nodes, fixed_nodes, data_nodes, free_nodes

    async def collect_skip_summaries(
        self,
        cache: ComposeTaskCache,
        skip_nodes: list[Any],
    ) -> tuple[list[dict[str, str]], int]:
        previous_summaries: list[dict[str, str]] = []
        skipped = 0
        for node in skip_nodes:
            draft = await self.read_current_draft(cache, node.id)
            if draft and draft.summary:
                previous_summaries.append({"title": node.title, "summary": draft.summary})
            skipped += 1
        return previous_summaries, skipped

    async def read_current_draft(self, cache: ComposeTaskCache, node_id: int) -> Any:
        hit, draft = cache.get_current_draft(node_id)
        if hit:
            return draft
        draft = await self.composer._load_current_draft(node_id)
        cache.set_current_draft(node_id, draft)
        return draft

    async def persist_section_draft(
        self,
        node: Any,
        payload: Any,
        *,
        gate_flags: Any = _GATE_FLAGS_UNSET,
    ) -> Any:
        resolved_flags = (
            getattr(payload, "gate_flags", None)
            if gate_flags is _GATE_FLAGS_UNSET
            else gate_flags
        )
        request = DraftWriteRequest(
            content=payload.content,
            source_material_ids=payload.source_material_ids,
            source_clause_ids=payload.source_clause_ids,
            gate_flags=resolved_flags,
            section_plan_id=payload.section_plan_id,
            planning_mode=payload.planning_mode,
            planning_failure_code=payload.planning_failure_code,
        )
        draft = await self.composer._save_draft(node, request)
        writer_compose_planning_mode_total.labels(
            planning_mode=planning_mode_label(getattr(payload, "planning_mode", None)),
            outcome="ok",
        ).inc()
        return draft

    async def compose_fixed_sections(self, ctx: ComposeRunContext) -> None:
        composer = self.composer
        logger.info(f"阶段1: fixed_template {len(ctx.fixed_nodes)}个")
        started = time.time()
        outcome = "ok"
        try:
            for node in ctx.fixed_nodes:
                payload = await composer._fill_template(node)
                draft = await self.persist_section_draft(node, payload)
                ctx.drafts.append(draft)
                ctx.done_count += 1
                composer._progress(
                    ctx.progress_callback,
                    "composing",
                    ctx.done_count,
                    ctx.total,
                    f"[固定模板] {node.title}",
                )
        except Exception:
            outcome = "error"
            raise
        finally:
            writer_compose_phase_seconds.labels(
                stage="compose", route="fixed_template", outcome=outcome
            ).observe(time.time() - started)

    async def compose_data_sections(self, ctx: ComposeRunContext) -> None:
        composer = self.composer
        logger.info(f"阶段2: data_fill {len(ctx.data_nodes)}个 (并行)")
        if not ctx.data_nodes:
            return
        t_data = time.time()
        outcome = "ok"
        data_draft_map: dict[int, Any] = {}

        async def _gen_data(index: int, node: Any) -> tuple[int, Any, Any]:
            payload = await composer._fill_data(
                node,
                ctx.industry_profile,
                ctx.number_map,
                project_id=ctx.project_id,
            )
            return index, node, payload

        data_tasks = [
            asyncio.create_task(_gen_data(index, node))
            for index, node in enumerate(ctx.data_nodes)
        ]
        try:
            for future in asyncio.as_completed(data_tasks):
                index, node, payload = await future
                verdicts = composer._run_writer_post_llm_gate(
                    ctx.project_id, node, payload
                )
                gate_flags = composer._aggregate_gate_flags(verdicts)
                cove_flags = await composer._maybe_run_writer_cove(
                    ctx.project_id, node, payload
                )
                gate_flags = composer._merge_gate_flags(gate_flags, cove_flags)
                draft = await self.persist_section_draft(
                    node, payload, gate_flags=gate_flags
                )
                await self.dispatch_writer_judge(ctx, node, payload)
                data_draft_map[index] = draft
                ctx.done_count += 1
                composer._progress(
                    ctx.progress_callback,
                    "composing",
                    ctx.done_count,
                    ctx.total,
                    f"[数据填充] {node.title}",
                )
                logger.info(
                    f"章节生成完成: {node.title} (data_fill, {draft.word_count}字)"
                )
        except Exception:
            outcome = "error"
            for task in data_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*data_tasks, return_exceptions=True)
            raise
        finally:
            writer_compose_phase_seconds.labels(
                stage="compose", route="data_fill", outcome=outcome
            ).observe(time.time() - t_data)
        ctx.drafts.extend(data_draft_map[idx] for idx in sorted(data_draft_map))
        logger.info(f"阶段2完成: {len(ctx.data_nodes)}个, 耗时={time.time()-t_data:.1f}s")

    async def compose_free_sections(self, ctx: ComposeRunContext) -> None:
        composer = self.composer
        logger.info(f"阶段3: free_writing {len(ctx.free_nodes)}个 (分组并行)")
        if not ctx.free_nodes:
            return
        t_free = time.time()
        outcome = "ok"
        groups: dict[int | None, list[Any]] = defaultdict(list)
        for node in ctx.free_nodes:
            groups[node.parent_id].append(node)
        for parent_id, group_nodes in groups.items():
            group_nodes.sort(key=lambda item: item.sort_order)
        group_keys = list(groups.keys())
        progress_lock = asyncio.Lock()

        async def _gen_group(parent_key: int | None) -> list[Any]:
            grp = groups[parent_key]
            grp_drafts: list[Any] = []
            local_summaries = list(ctx.previous_summaries)
            for node in grp:
                await await_node_plan_prefetch(ctx.plan_prefetch_tasks, node.id)
                payload = await composer._compose_free(
                    node,
                    ctx.industry_profile,
                    local_summaries,
                    ctx.number_map,
                )
                verdicts = composer._run_writer_post_llm_gate(
                    ctx.project_id, node, payload
                )
                gate_flags = composer._aggregate_gate_flags(verdicts)
                cove_flags = await composer._maybe_run_writer_cove(
                    ctx.project_id, node, payload
                )
                gate_flags = composer._merge_gate_flags(gate_flags, cove_flags)
                draft = await self.persist_section_draft(
                    node, payload, gate_flags=gate_flags
                )
                grp_drafts.append(draft)
                summary = await composer._generate_summary(node.title, payload.content)
                async with composer._db_lock:
                    draft.summary = summary
                    await composer.db.commit()
                writer_compose_commit_total.labels(
                    kind="draft_summary", outcome="ok"
                ).inc()
                local_summaries.append({"title": node.title, "summary": summary})
                await self.dispatch_writer_judge(ctx, node, payload)
                async with progress_lock:
                    ctx.done_count += 1
                    composer._progress(
                        ctx.progress_callback,
                        "composing",
                        ctx.done_count,
                        ctx.total,
                        f"[自由论述] {node.title}",
                    )
                logger.info(
                    f"章节生成完成: {node.title} (free_writing, {draft.word_count}字)"
                )
            return grp_drafts

        group_tasks = [asyncio.create_task(_gen_group(pk)) for pk in group_keys]
        try:
            group_results = await asyncio.gather(*group_tasks)
        except Exception:
            outcome = "error"
            for task in group_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*group_tasks, return_exceptions=True)
            raise
        finally:
            writer_compose_phase_seconds.labels(
                stage="compose", route="free_writing", outcome=outcome
            ).observe(time.time() - t_free)
        for grp_drafts in group_results:
            ctx.drafts.extend(grp_drafts)
        logger.info(
            f"阶段3完成: {len(ctx.free_nodes)}个, {len(group_keys)}组并行, "
            f"耗时={time.time()-t_free:.1f}s"
        )

    async def dispatch_writer_judge(
        self,
        ctx: ComposeRunContext,
        node: Any,
        payload: Any,
    ) -> None:
        if not ctx.defer_judges:
            await self.composer._maybe_run_writer_judge(ctx.project_id, node, payload)
            return
        checker = getattr(self.composer, "_is_writer_critical_commitment", None)
        try:
            is_high_risk, _ = checker(node, payload) if callable(checker) else (False, "")
        except Exception:
            is_high_risk = False
        snapshot = snapshot_from_compose(
            project_id=ctx.project_id,
            node=node,
            payload=payload,
            is_high_risk=bool(is_high_risk),
        )
        if ctx.judge_batch is not None:
            ctx.judge_batch.enqueue_judge(snapshot)
            writer_compose_judge_total.labels(kind="enqueue", outcome="ok").inc()

    async def drain_deferred_judges(self, ctx: ComposeRunContext) -> None:
        """
        职责: 有界 drain 延后 Judge 队列。
        不做: 不改正文、不把 CoVe 移出关键路径。
        """
        if not ctx.defer_judges or ctx.judge_batch is None:
            return
        from app.core.writer.composer.gate_runner import run_writer_judge_from_snapshot

        try:
            await ctx.judge_batch.drain_judges(
                lambda snapshot: run_writer_judge_from_snapshot(self.composer, snapshot)
            )
            writer_compose_judge_total.labels(kind="drain", outcome="ok").inc()
        except Exception as exc:
            logger.warning("Writer Judge drain degraded: {}", exc)
            writer_compose_judge_total.labels(kind="drain", outcome="error").inc()
            await ctx.judge_batch.cancel_judges()

    def finalize_compose(self, ctx: ComposeRunContext) -> list[Any]:
        composer = self.composer
        total_elapsed = time.time() - ctx.compose_start
        if ctx.skipped > 0:
            logger.info(
                "跳过受保护章节: {}/{} ({})",
                ctx.skipped,
                ctx.total,
                ", ".join(node.title for node in ctx.skip_nodes[:10]),
            )
        composer._progress(
            ctx.progress_callback, "completed", ctx.total, ctx.total, "全部章节生成完成"
        )
        logger.info(
            f"内容生成完成: project={ctx.project_id}, "
            f"drafts={len(ctx.drafts)}, skipped={ctx.skipped}, "
            f"总耗时={total_elapsed:.1f}s"
        )
        writer_compose_phase_seconds.labels(
            stage="finalize", route="all", outcome="ok"
        ).observe(total_elapsed)
        return ctx.drafts

    @staticmethod
    def flush_cache_metrics(cache: ComposeTaskCache) -> None:
        for kind, count in cache.hits.items():
            if count:
                writer_compose_cache_total.labels(kind=kind, outcome="hit").inc(count)
        for kind, count in cache.misses.items():
            if count:
                writer_compose_cache_total.labels(kind=kind, outcome="miss").inc(count)
