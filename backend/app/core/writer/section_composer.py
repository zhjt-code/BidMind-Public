# Compatibility facade: composer helpers and tests use module-level patch targets.
# ruff: noqa: F401
"""章节内容生成编排器

三分法路由:
  fixed_template -> 纯算法占位符替换
  data_fill      -> 轻量AI将结构化数据转段落/表格
  free_writing   -> 完整AI自由论述 + token预算管理

按阅读顺序逐章生成，每章生成后产出摘要供后续章节参考（摘要链）。
"""

import asyncio
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from html import escape
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import LLMClient, get_review_client
from app.core.ai.critic.flows import FLOW_WRITER
from app.core.ai.input_guard import retry_on_input_too_long_400
from app.core.ai.prompt_registry import call_with_fallback
from app.core.config import settings
from app.core.observability.instrument import instrument as _observability_instrument
from app.core.writer.composer import (
    binding_loader,
    data_fill_route,
    draft_persistence,
    free_writing_route,
    gate_runner,
    material_context,
    skip_policy,
    template_route,
)
from app.core.writer.material_titles import get_effective_material_title
from app.core.writer.numbering import (
    compute_chapter_numbers,
    format_number_for_depth,
    get_sub_numbering_hint,
    strip_existing_number,
)
from app.core.writer.prompts.section_compose import (
    get_data_fill_prompt as _code_data_fill,
)
from app.core.writer.prompts.section_compose import (
    get_free_writing_prompt as _code_free_writing,
)
from app.core.writer.prompts.section_compose import (
    get_summarize_prompt as _code_summarize,
)
from app.core.writer.section_structure import normalize_section_html

# L-7: 真实定义迁至 text_budget；保留 _shrink_semantic_text 同名符号，
# composer/free_writing_route 与 data_fill_route 以函数级 import 消费（monkeypatch 契约）。
from app.core.writer.text_budget import shrink_semantic_text as _shrink_semantic_text

# NOTE: 以下 wrapper 直接走 prompt_registry.call_with_fallback, 通过
# inspect.signature 自动把位置参数映射成命名参数后再 format, 避免 Registry 命中
# 命名占位符但调用方传位置参数时发生静默 fallback (的根治版)。


def get_data_fill_prompt(*a, **kw):
    # 该节点不经 call_with_fallback: Registry 命中时它直接格式化线上模板，
    # 多余入参被静默忽略，会使 _code_data_fill 内的"线上模板缺占位符则补挂"
    # 失效并丢弃行业画像（同源）。_code_data_fill 自身已是 Registry
    # 优先实现，现有调用方全部使用关键字参数，无需 的位置参数映射。
    return _code_data_fill(*a, **kw)

def get_free_writing_prompt(*a, **kw):
    return call_with_fallback("writing.free_writing", _code_free_writing, *a, **kw)

def get_summarize_prompt(*a, **kw):
    return call_with_fallback("writing.section_summary", _code_summarize, *a, **kw)
from app.core.writer.image_node_affinity import filter_images_for_node
from app.models.writing_binding import BindingStatus, BindingType, WritingBinding
from app.models.writing_clause import WritingClause, WritingClauseLayer
from app.models.writing_material import WritingMaterial
from app.models.writing_material_chunk import WritingMaterialChunk
from app.models.writing_material_image import (
    STRUCTURABLE_IMAGE_TYPES,
    WritingMaterialImage,
)
from app.models.writing_outline_node import (
    OutlineNodeStatus,
    OutlineNodeType,
    WritingOutlineNode,
)
from app.models.writing_section_draft import DraftStatus, WritingSectionDraft

ProgressCallback = Callable[..., None]

# B2.5: DeAIFilter metrics-only (不修改内容，仅统计 AI 套话数量)
from app.core.observability.metrics import ai_phrase_count_total
from app.core.writer.de_ai_filter import DeAIFilter

_de_ai_filter = DeAIFilter()


def _observe_ai_phrases(text: str) -> None:
    """B2.5 — 统计 free_writing 输出的 AI 套话数（纯只读，不改 text）。"""
    count = _de_ai_filter.get_ai_phrase_count(text)
    if count > 0:
        ai_phrase_count_total.labels(flow="writer", node_type="free_writing").inc(count)


_CHARS_PER_TOKEN = 2

_PLACEHOLDER_FALLBACK_RE = re.compile(r"\{([\u4e00-\u9fff\w]{1,20})\}")

_PLACEHOLDER_WHITELIST_MAP: dict[str, str] = {
    "{公司名称}": "[待补充:公司名称]",
    "{投标人}": "[待补充:投标人名称]",
    "{项目名称}": "[待补充:项目名称]",
    "{日期}": "[待补充:日期]",
    "{法定代表人}": "[待补充:法定代表人]",
    "{联系电话}": "[待补充:联系电话]",
    "{地址}": "[待补充:地址]",
}


def sanitize_residual_placeholders(text: str) -> str:
    """统一清理残留的 {xxx} 花括号占位符为 [待补充:xxx]。

 设计动机: 仅在 fixed_template 路径替换占位符,
 LLM 输出路径(data_fill/free_writing)与 Word/HTML 渲染层均无兜底,
 导致 LLM 从素材中复读的 {xxx} 与历史脏 draft 直接泄漏到最终标书。
 本函数作为模块级"出口闸门",所有落库与渲染入口统一调用,确保:
 - 白名单优先映射(保留人类可读语义,如 投标人 -> 投标人名称)
 - 通用正则兜底(中文/字母/数字/下划线 1-20 字符)
 - 幂等(对 [待补充:xxx] 已替换内容二次调用结果不变)
 """
    if not text:
        return text
    for placeholder, replacement in _PLACEHOLDER_WHITELIST_MAP.items():
        text = text.replace(placeholder, replacement)
    text = _PLACEHOLDER_FALLBACK_RE.sub(r"[待补充:\1]", text)
    return text


# ==================== 素材上下文常量（Task 6 Step 3 re-export）====================
# 真实定义在 ``app.core.writer.composer.material_context``；保留此 re-export
# 是为了让测试 (test_section_composer_route_snapshot 等) 仍可通过
# ``from app.core.writer.section_composer import _LONG_TEXT_SPECIALIZATION_RULES``
# 直接访问。所有数值/键集合与原版字节级一致。
_DEFAULT_MATERIAL_CHUNK_SAMPLE_LIMIT = material_context.DEFAULT_MATERIAL_CHUNK_SAMPLE_LIMIT
_DEFAULT_CHUNK_EXCERPT_CHARS = material_context.DEFAULT_CHUNK_EXCERPT_CHARS
_DEFAULT_CONTENT_FALLBACK_CHARS = material_context.DEFAULT_CONTENT_FALLBACK_CHARS
_HIGH_VALUE_TEXT_MATERIAL_CHUNK_SAMPLE_LIMIT = material_context.HIGH_VALUE_TEXT_MATERIAL_CHUNK_SAMPLE_LIMIT
_HIGH_VALUE_TEXT_CHUNK_EXCERPT_CHARS = material_context.HIGH_VALUE_TEXT_CHUNK_EXCERPT_CHARS
_HIGH_VALUE_TEXT_FALLBACK_CHARS = material_context.HIGH_VALUE_TEXT_FALLBACK_CHARS
_HIGH_VALUE_TEXT_DIRECT_PARAGRAPH_LIMIT = material_context.HIGH_VALUE_TEXT_DIRECT_PARAGRAPH_LIMIT
_HIGH_VALUE_TEXT_DIRECT_PARAGRAPH_CHARS = material_context.HIGH_VALUE_TEXT_DIRECT_PARAGRAPH_CHARS

_LONG_TEXT_SPECIALIZATION_RULES = material_context.LONG_TEXT_SPECIALIZATION_RULES


def _shrink_summary_items(summaries: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(summaries) <= 3:
        return list(summaries)
    drop_count = max(1, len(summaries) // 3)
    keep_count = max(3, len(summaries) - drop_count)
    return list(summaries[-keep_count:])


@dataclass
class ComposePayload:
    """章节生成结果及其来源痕迹"""
    content: str
    source_material_ids: list[int]
    source_clause_ids: list[int]
    gate_flags: Optional[dict[str, Any]] = None
    bound_material_ids: Optional[list[int]] = None
    selected_material_ids: Optional[list[int]] = None
    consumption_trace: Optional[dict[str, Any]] = None
    section_plan_id: Optional[int] = None
    planning_mode: Optional[str] = None
    planning_failure_code: Optional[str] = None


@dataclass
class MaterialContextBuildResult:
    """素材上下文与可观测性统计"""
    text: str
    used_material_ids: list[int]
    bound_materials: int
    used_materials: int
    priority_materials_used: int
    priority_segments_used: int
    truncated_materials: int


class SectionComposer:
    """章节内容生成编排器 -- 三分法路由"""

    # 模板常量 ClassVar re-export，保留 ``cls._TEMPLATE_*`` 访问路径
    _TEMPLATE_SYMBOL_ONLY_RE = template_route.TEMPLATE_SYMBOL_ONLY_RE
    _TEMPLATE_NUMBER_STUB_RE = template_route.TEMPLATE_NUMBER_STUB_RE
    _TABULAR_TEMPLATE_TITLE_KEYWORDS = template_route.TABULAR_TEMPLATE_TITLE_KEYWORDS

    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        project_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self._db_lock = asyncio.Lock()
        self.project_id = project_id
        self.llm_client = llm_client or get_review_client(project_id=project_id)
        self._material_pool_size_cache: dict[int, int] = {}

    async def _ensure_material_pool_size(self, project_id: int) -> int:
        """S.2.1: 按项目缓存素材池大小，供 MaterialSourceTraceGate 区分
        material_pool_empty (pool=0) 与 no_binding (pool>0 但本章未绑定)。
        """
        if project_id in self._material_pool_size_cache:
            return self._material_pool_size_cache[project_id]
        try:
            from sqlalchemy import func
            result = await self.db.execute(
                select(func.count(WritingMaterial.id)).where(
                    WritingMaterial.project_id == project_id,
                )
            )
            size = int(result.scalar_one() or 0)
        except Exception as e:
            logger.warning(
                f"_ensure_material_pool_size degraded: project={project_id}, err={e}"
            )
            size = 0
        self._material_pool_size_cache[project_id] = size
        return size

    async def _chat_with_input_guard(
        self,
        *,
        operation_name: str,
        initial_payload: dict[str, object],
        render_prompt: Callable[[dict[str, object]], tuple[str, str]],
        reduce_payload_semantically: Callable[[dict[str, object], int], tuple[dict[str, object], dict[str, object] | None]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        async def _send_request(payload: dict[str, object]):
            system_msg, user_msg = render_prompt(payload)
            return await self.llm_client.chat(
                user_message=user_msg,
                system_message=system_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        return await retry_on_input_too_long_400(
            operation_name=operation_name,
            initial_payload=initial_payload,
            send_request=_send_request,
            reduce_payload_semantically=reduce_payload_semantically,
            max_retries=1,
            describe_payload=lambda payload: {
                key: len(value)
                for key, value in payload.items()
                if isinstance(value, str)
            },
        )

    # ==================== 主入口 ====================

    @_observability_instrument(
        name="writer_compose_all_seconds",
        layer="stage",
        pillar="efficiency",
        direction="lower_better",
        business="writer",
        measure="latency",
        description="标书章节编排器 compose_all 单次执行耗时",
    )
    async def compose_all(
        self,
        project_id: int,
        progress_callback: Optional[ProgressCallback] = None,
        skip_existing_drafts: bool = False,
    ) -> list[WritingSectionDraft]:
        """为所有叶子节点生成内容草稿。"""
        from app.core.writer.composer.compose_pipeline import (
            ComposePipeline,
            ComposeSectionsRequest,
        )

        return await ComposePipeline(self).compose_sections(
            ComposeSectionsRequest(
                project_id=project_id,
                progress_callback=progress_callback,
                skip_existing_drafts=skip_existing_drafts,
            )
        )

    async def compose_single(
        self,
        node: WritingOutlineNode,
        project_id: int,
    ) -> WritingSectionDraft:
        """单章节生成/重新生成"""
        all_nodes = await self._load_all_outline_nodes(project_id)
        number_map = compute_chapter_numbers(all_nodes)

        industry_profile = await self._load_industry_profile(project_id)
        previous_summaries = await self._build_previous_summaries(project_id, node)
        await self._ensure_material_pool_size(project_id)

        if node.node_type == OutlineNodeType.fixed_template:
            payload = await self._fill_template(node)
        elif node.node_type == OutlineNodeType.data_fill:
            payload = await self._fill_data(node, industry_profile, number_map, project_id=project_id)
        else:
            payload = await self._compose_free(
                node, industry_profile, previous_summaries, number_map
            )

        # B5.6: Writer Gate Pipeline (post_llm) - 单章粒度
        # P2.1b: verdict 聚合为 gate_flags, 由 _save_draft 持久化
        verdicts = self._run_writer_post_llm_gate(project_id, node, payload)
        gate_flags = self._aggregate_gate_flags(verdicts)
        gate_flags = self._merge_gate_flags(gate_flags, payload.gate_flags)
        # 关键承诺章节追加 CoVe 复核, 与 Gate flags 一次落库
        cove_flags = await self._maybe_run_writer_cove(project_id, node, payload)
        gate_flags = self._merge_gate_flags(gate_flags, cove_flags)

        draft = await self._save_draft(
            node,
            draft_persistence.DraftWriteRequest(
                content=payload.content,
                source_material_ids=payload.source_material_ids,
                source_clause_ids=payload.source_clause_ids,
                gate_flags=gate_flags,
                section_plan_id=payload.section_plan_id,
                planning_mode=payload.planning_mode,
                planning_failure_code=payload.planning_failure_code,
            ),
        )
        summary = await self._generate_summary(node.title, payload.content)
        async with self._db_lock:
            draft.summary = summary
            await self.db.commit()

        # B5.12: Judge 抽样接入 (写标)
        await self._maybe_run_writer_judge(project_id, node, payload)

        return draft

    # ==================== Gate / CoVe / Judge 后置评审（Task 6 Step 1）====================
    # 真实实现已搬到 ``app.core.writer.composer.gate_runner``；以下保留 thin
    # forwarder 是为了：(a) 维持 ``compose_all`` / ``compose_single`` 内部
    # ``self._run_writer_post_llm_gate(...)`` 等调用路径；(b) 兼容测试通过
    # ``SectionComposer._aggregate_gate_flags`` / ``composer._build_writer_judge_evidence``
    # = AsyncMock(...) 的 monkey patch 契约。

    # 关键承诺关键词 ClassVar：被 test_section_composer_route_snapshot 直接访问，
    # 同时供 gate_runner.is_writer_critical_commitment 与子类继承。
    _WRITER_CRITICAL_TITLE_KEYWORDS: tuple[str, ...] = gate_runner.WRITER_CRITICAL_TITLE_KEYWORDS
    _WRITER_COMMITMENT_KEYWORDS: tuple[str, ...] = gate_runner.WRITER_COMMITMENT_KEYWORDS

    def _run_writer_post_llm_gate(
        self,
        project_id: int,
        node: WritingOutlineNode,
        payload: ComposePayload,
    ) -> list:
        return gate_runner.run_writer_post_llm_gate(self, project_id, node, payload)

    @staticmethod
    def _aggregate_gate_flags(verdicts: list) -> Optional[dict]:
        return gate_runner.aggregate_gate_flags(verdicts)

    @classmethod
    def _is_writer_critical_commitment(
        cls,
        node: WritingOutlineNode,
        payload: "ComposePayload",
    ) -> tuple[bool, str]:
        return gate_runner.is_writer_critical_commitment(node, payload)

    async def _maybe_run_writer_cove(
        self,
        project_id: int,
        node: WritingOutlineNode,
        payload: "ComposePayload",
    ) -> Optional[dict]:
        return await gate_runner.maybe_run_writer_cove(self, project_id, node, payload)

    @staticmethod
    def _merge_gate_flags(
        base: Optional[dict], extra: Optional[dict],
    ) -> Optional[dict]:
        return gate_runner.merge_gate_flags(base, extra)

    async def _maybe_run_writer_judge(
        self,
        project_id: int,
        node: WritingOutlineNode,
        payload: ComposePayload,
    ) -> None:
        await gate_runner.maybe_run_writer_judge(self, project_id, node, payload)

    async def _build_writer_judge_evidence(
        self,
        *,
        node_id: int,
        source_material_ids: list[int],
        source_clause_ids: list[int],
    ) -> dict:
        return await gate_runner.build_writer_judge_evidence(
            self,
            node_id=node_id,
            source_material_ids=source_material_ids,
            source_clause_ids=source_clause_ids,
        )

    # ==================== 路径1: fixed_template thin forwarder（Task 6 Step 5）====================
    # 真实实现搬到 ``app.core.writer.composer.template_route``。
    # 主类保留 classmethod thin forwarder 以兼容 ``test_writer_numbering`` 等测试
    # 通过 ``SectionComposer._normalize_fixed_template_text`` 直接调用。

    # 模板常量 ClassVar（re-export，保留 ``cls._TEMPLATE_*`` 与 cls._AUTOFILL_TYPE_WHITELIST 访问路径）
    _AUTOFILL_TYPE_WHITELIST: dict[str, list[str]] = template_route.AUTOFILL_TYPE_WHITELIST

    async def _fill_template(self, node: WritingOutlineNode) -> ComposePayload:
        return await template_route.fill_template(self, node)

    def _replace_placeholders(self, template: str) -> str:
        return template_route.replace_placeholders(template)

    @classmethod
    def _autofill_structured_fields(
        cls,
        template: str,
        images: list[WritingMaterialImage],
        node_title: str = "",
    ) -> str:
        return template_route.autofill_structured_fields(template, images, node_title)

    @classmethod
    def _is_autofill_compatible(cls, image_type: str, node_title_lower: str) -> bool:
        return template_route.is_autofill_compatible(image_type, node_title_lower)

    @staticmethod
    def _append_figure_tags(
        html: str, images: list[WritingMaterialImage],
    ) -> str:
        return template_route.append_figure_tags(html, images)

    @classmethod
    def _normalize_fixed_template_text(cls, template: str) -> str:
        return template_route.normalize_fixed_template_text(template)

    @classmethod
    def _is_tabular_fixed_template(cls, title: str, template: str) -> bool:
        return template_route.is_tabular_fixed_template(title, template)

    @classmethod
    def _rebuild_tabular_fixed_template(cls, title: str, template: str) -> str:
        return template_route.rebuild_tabular_fixed_template(title, template)

    @classmethod
    def _rebuild_bid_opening_table_html(cls, template: str) -> str:
        return template_route.rebuild_bid_opening_table_html(template)

    @classmethod
    def _rebuild_generic_table_html(
        cls,
        template: str,
        *,
        headers: tuple[str, ...],
        row_placeholders: tuple[str, ...],
    ) -> str:
        return template_route.rebuild_generic_table_html(
            template, headers=headers, row_placeholders=row_placeholders,
        )

    @staticmethod
    def _split_template_lines(template: str) -> list[str]:
        return template_route.split_template_lines(template)

    @staticmethod
    def _normalize_table_note(line: str) -> str:
        return template_route.normalize_table_note(line)

    @staticmethod
    def _looks_like_numbered_line(line: str) -> bool:
        return template_route.looks_like_numbered_line(line)

    @staticmethod
    def _looks_like_bid_opening_table_fragment(line: str) -> bool:
        return template_route.looks_like_bid_opening_table_fragment(line)

    @staticmethod
    def _looks_like_generic_table_fragment(
        line: str,
        headers: tuple[str, ...],
    ) -> bool:
        return template_route.looks_like_generic_table_fragment(line, headers)

    @staticmethod
    def _normalize_placeholder_text(text: str) -> str:
        return template_route.normalize_placeholder_text(text)

    @classmethod
    def _normalize_template_parenthesized_item(
        cls,
        line: str,
        next_line: str,
    ) -> tuple[str, bool]:
        return template_route.normalize_template_parenthesized_item(line, next_line)

    @staticmethod
    def _inject_template_suffix_value(text: str, suffix_value: str) -> str:
        return template_route.inject_template_suffix_value(text, suffix_value)

    @staticmethod
    def _normalize_template_stub_spacing(line: str) -> str:
        return template_route.normalize_template_stub_spacing(line)

    @classmethod
    def _should_join_template_line(cls, previous: str, current: str) -> bool:
        return template_route.should_join_template_line(previous, current)

    # ==================== 路径2: data_fill thin forwarder（Task 6 Step 6）====================
    # 真实实现搬到 ``app.core.writer.composer.data_fill_route``。

    def _try_data_fill_template(
        self,
        node: WritingOutlineNode,
        project_id: Optional[int],
        materials: list,
        chunk_samples: Any,
        clauses: list,
        images: Optional[list] = None,
    ) -> Optional[ComposePayload]:
        return data_fill_route.try_data_fill_template(
            self, node, project_id, materials, chunk_samples, clauses, images,
        )

    def _extract_template_items(
        self,
        materials: list,
        chunk_samples: Any,
        template_name: str,
        *,
        images: Optional[list] = None,
    ) -> list[dict[str, Any]]:
        return data_fill_route.extract_template_items(
            materials, chunk_samples, template_name, images=images,
        )

    async def _fill_data(
        self,
        node: WritingOutlineNode,
        industry_profile: Optional[str],
        number_map: Optional[dict[int, str]] = None,
        project_id: Optional[int] = None,
    ) -> ComposePayload:
        return await data_fill_route.fill_data(
            self, node, industry_profile, number_map, project_id,
        )

    # ==================== 路径3: free_writing thin forwarder（Task 6 Step 7）====================
    # 真实实现搬到 ``app.core.writer.composer.free_writing_route``。

    async def _compose_free(
        self,
        node: WritingOutlineNode,
        industry_profile: Optional[str],
        previous_summaries: list[dict[str, str]],
        number_map: Optional[dict[int, str]] = None,
    ) -> ComposePayload:
        return await free_writing_route.compose_free(
            self, node, industry_profile, previous_summaries, number_map,
        )

    def _build_context_within_budget(
        self,
        industry_profile: Optional[str],
        clauses: list[WritingClause],
        materials: list[WritingMaterial],
        previous_summaries: list[dict[str, str]],
        images: Optional[list[WritingMaterialImage]] = None,
        chunk_samples: Optional[dict[int, list[str]]] = None,
        chapter_profile: Optional[dict[str, Any]] = None,
        node_title: str = "",
    ) -> dict[str, str | list[int] | int]:
        return free_writing_route.build_context_within_budget(
            self,
            industry_profile,
            clauses,
            materials,
            previous_summaries,
            images=images,
            chunk_samples=chunk_samples,
            chapter_profile=chapter_profile,
            node_title=node_title,
        )

    async def _generate_summary(self, title: str, content: str) -> str:
        return await free_writing_route.generate_summary(self, title, content)

    # ==================== DB 读写 ====================

    # ==================== 绑定 / 章节加载 thin forwarder（Task 6 Step 4）====================
    # 真实实现搬到 ``app.core.writer.composer.binding_loader``。

    async def _load_all_outline_nodes(
        self, project_id: int,
    ) -> list[WritingOutlineNode]:
        return await binding_loader.load_all_outline_nodes(self, project_id)

    async def _load_outline_leaves(
        self, project_id: int,
    ) -> list[WritingOutlineNode]:
        return await binding_loader.load_outline_leaves(self, project_id)

    def _sort_by_reading_order(
        self, nodes: list[WritingOutlineNode],
    ) -> list[WritingOutlineNode]:
        return binding_loader.sort_by_reading_order(nodes)

    async def _load_industry_profile(self, project_id: int) -> Optional[str]:
        return await binding_loader.load_industry_profile(self, project_id)

    async def _load_current_draft(
        self, node_id: int,
    ) -> Optional[WritingSectionDraft]:
        cache = getattr(self, "_compose_cache", None)
        if cache is not None:
            hit, draft = cache.get_current_draft(node_id)
            if hit:
                return draft
        draft = await binding_loader.load_current_draft(self, node_id)
        if cache is not None:
            cache.set_current_draft(node_id, draft)
        return draft

    async def _load_bound_materials(
        self,
        node_id: int,
        *,
        node_title: str = "",
        chapter_profile: Optional[dict[str, Any]] = None,
    ) -> list[WritingMaterial]:
        return await binding_loader.load_bound_materials(
            self, node_id, node_title=node_title, chapter_profile=chapter_profile,
        )

    async def _load_bound_clauses(
        self, node_id: int, *, include_inherited: bool = True,
    ) -> list[WritingClause]:
        return await binding_loader.load_bound_clauses(
            self, node_id, include_inherited=include_inherited,
        )

    async def _load_ancestor_node_ids(self, node_id: int) -> list[int]:
        return await binding_loader.load_ancestor_node_ids(self, node_id)

    @staticmethod
    def _status_to_str(status: object) -> str:
        return binding_loader.status_to_str(status)

    @classmethod
    def _select_consumable_bindings(
        cls,
        bindings: list[WritingBinding],
    ) -> list[WritingBinding]:
        return binding_loader.select_consumable_bindings(bindings)

    @classmethod
    def _collect_binding_targets(
        cls,
        binding_groups: list[list[WritingBinding]],
        target_attr: str,
    ) -> list[int]:
        return binding_loader.collect_binding_targets(binding_groups, target_attr)

    async def _find_bound_clause(
        self,
        node_id: int,
        sub_type: str,
        node_title: str = "",
        node_volume_type: Optional[str] = None,
    ) -> Optional[WritingClause]:
        return await binding_loader.find_bound_clause(
            self, node_id, sub_type, node_title, node_volume_type,
        )

    async def _find_fallback_template_clause(
        self,
        project_id: int,
        node_title: str,
        node_volume_type: Optional[str] = None,
    ) -> Optional[WritingClause]:
        return await binding_loader.find_fallback_template_clause(
            self, project_id, node_title, node_volume_type,
        )

    @staticmethod
    def _normalize_fixed_template_lookup(text: str) -> str:
        return binding_loader.normalize_fixed_template_lookup(text)

    @staticmethod
    def _extract_fixed_template_keywords(text: str) -> set[str]:
        return binding_loader.extract_fixed_template_keywords(text)

    async def _build_previous_summaries(
        self, project_id: int, current_node: WritingOutlineNode,
    ) -> list[dict[str, str]]:
        return await binding_loader.build_previous_summaries(
            self, project_id, current_node,
        )

    async def _save_draft(
        self,
        node: WritingOutlineNode,
        content: str | draft_persistence.DraftWriteRequest,
        *,
        source_material_ids: Optional[list[int]] = None,
        source_clause_ids: Optional[list[int]] = None,
        gate_flags: Optional[dict] = None,
        section_plan_id: Optional[int] = None,
        planning_mode: Optional[str] = None,
        planning_failure_code: Optional[str] = None,
    ) -> WritingSectionDraft:
        """草稿落库 thin forwarder（Task 6 Step 2，真实实现见 composer.draft_persistence）。

        thin forwarder 保留原因：测试通过 ``composer._save_draft = AsyncMock(...)``
        模式替换实例方法，主类必须保留可被 monkey patch 的同名方法。
        """
        return await draft_persistence.save_draft(
            self,
            node,
            content,
            source_material_ids=source_material_ids,
            source_clause_ids=source_clause_ids,
            gate_flags=gate_flags,
            section_plan_id=section_plan_id,
            planning_mode=planning_mode,
            planning_failure_code=planning_failure_code,
        )

    # ==================== 素材上下文 thin forwarder（Task 6 Step 3）====================
    # 真实实现搬到 ``app.core.writer.composer.material_context``；以下 thin
    # forwarder 维持：(a) ``self._X(...)`` 内部调用路径；(b) 测试通过
    # ``SectionComposer._collect_binding_targets`` / ``_is_material_consumable`` /
    # ``_format_chapter_profile_text`` / ``_parse_profile`` 等 staticmethod /
    # classmethod 直接访问；(c) ``composer._build_material_context_entry =
    # AsyncMock(...)`` 实例方法 monkey patch。

    # ----- DB 加载 -----

    async def _load_material_images(
        self, materials: list[WritingMaterial],
    ) -> list[WritingMaterialImage]:
        cache = getattr(self, "_compose_cache", None)
        if cache is None or not materials:
            return await material_context.load_material_images(self, materials)
        cached_images: list[WritingMaterialImage] = []
        missing: list[WritingMaterial] = []
        for material in materials:
            hit, images = cache.get_image_metadata(material.id)
            if hit:
                cached_images.extend(images or [])
            else:
                missing.append(material)
        fetched: list[WritingMaterialImage] = []
        if missing:
            fetched = await material_context.load_material_images(self, missing)
            grouped: dict[int, list[WritingMaterialImage]] = defaultdict(list)
            for image in fetched:
                grouped[int(image.material_id)].append(image)
            for material in missing:
                cache.set_image_metadata(material.id, grouped.get(material.id, []))
        merged = [*cached_images, *fetched]
        merged.sort(
            key=lambda img: (
                int(img.material_id),
                int(getattr(img, "image_index", 0) or 0),
            )
        )
        return merged

    async def _load_material_chunk_samples(
        self,
        materials: list[WritingMaterial],
        node_title: str,
        chapter_profile: Optional[dict[str, Any]] = None,
    ) -> dict[int, list[str]]:
        samples = await material_context.load_material_chunk_samples(
            self, materials, node_title, chapter_profile,
        )
        cache = getattr(self, "_compose_cache", None)
        if cache is not None and materials:
            for _material in materials:
                cache.record_chunk_access(hit=False)
        return samples

    # ----- staticmethod / 纯函数 forwarder -----

    @staticmethod
    def _format_image_metadata(images: list[WritingMaterialImage]) -> str:
        return material_context.format_image_metadata(images)

    @staticmethod
    def _parse_material_tags(material: WritingMaterial) -> dict[str, Any]:
        return material_context.parse_material_tags(material)

    @staticmethod
    def _parse_profile(node: WritingOutlineNode) -> Optional[dict[str, Any]]:
        return material_context.parse_profile(node)

    @staticmethod
    def _extract_keyword_terms(text: str) -> set[str]:
        return material_context.extract_keyword_terms(text)

    @staticmethod
    def _material_section_hint_matches(tags: dict[str, Any], node_title: str) -> bool:
        return material_context.material_section_hint_matches(tags, node_title)

    @staticmethod
    def _format_chapter_profile_text(
        chapter_profile: Optional[dict[str, Any]],
    ) -> str:
        return material_context.format_chapter_profile_text(chapter_profile)

    @staticmethod
    def _dedupe_ids(ids: list[Optional[int]]) -> list[int]:
        return material_context.dedupe_ids(ids)

    @classmethod
    def _serialize_source_ids(cls, ids: Optional[list[int]]) -> Optional[str]:
        return material_context.serialize_source_ids(ids)

    @staticmethod
    def _dedupe_preserve_order(values: list[str]) -> list[str]:
        return material_context.dedupe_preserve_order(values)

    @staticmethod
    def _truncate_material_context_entry(entry: str, max_chars: int) -> str:
        return material_context.truncate_material_context_entry(entry, max_chars)

    @staticmethod
    def _count_priority_segments_in_entry(entry: str) -> int:
        return material_context.count_priority_segments_in_entry(entry)

    @staticmethod
    def _is_material_consumable(material: WritingMaterial) -> bool:
        return material_context.is_material_consumable(material)

    @staticmethod
    def _entry_contains_priority_segments(entry: str) -> bool:
        return material_context.entry_contains_priority_segments(entry)

    # ----- 实例方法（需访问 composer 状态）forwarder -----

    def _is_high_value_text_material(self, material: WritingMaterial) -> bool:
        return material_context.is_high_value_text_material(self, material)

    def _get_long_text_specialization(
        self,
        material: WritingMaterial,
        *,
        tags: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        return material_context.get_long_text_specialization(self, material, tags=tags)

    def _get_material_chunk_sample_limit(self, material: WritingMaterial) -> int:
        return material_context.get_material_chunk_sample_limit(self, material)

    def _get_material_excerpt_chars(self, material: WritingMaterial) -> int:
        return material_context.get_material_excerpt_chars(self, material)

    def _get_material_fallback_chars(self, material: WritingMaterial) -> int:
        return material_context.get_material_fallback_chars(self, material)

    def _build_priority_text_material_segments(
        self,
        material: WritingMaterial,
        *,
        node_title: str = "",
        chapter_profile: Optional[dict[str, Any]] = None,
    ) -> list[str]:
        return material_context.build_priority_text_material_segments(
            self, material, node_title=node_title, chapter_profile=chapter_profile,
        )

    def _filter_inherited_materials_for_node(
        self,
        materials: list[WritingMaterial],
        *,
        node_title: str,
        chapter_profile: Optional[dict[str, Any]] = None,
    ) -> list[WritingMaterial]:
        return material_context.filter_inherited_materials_for_node(
            self, materials, node_title=node_title, chapter_profile=chapter_profile,
        )

    def _score_inherited_material_for_node(
        self,
        material: WritingMaterial,
        *,
        node_title: str,
        chapter_profile: Optional[dict[str, Any]] = None,
    ) -> int:
        return material_context.score_inherited_material_for_node(
            self, material, node_title=node_title, chapter_profile=chapter_profile,
        )

    def _format_materials(
        self,
        materials: list[WritingMaterial],
        *,
        chunk_samples: Optional[dict[int, list[str]]] = None,
        node_title: str = "",
        chapter_profile: Optional[dict[str, Any]] = None,
        max_total_chars: Optional[int] = None,
    ) -> MaterialContextBuildResult:
        return material_context.format_materials(
            self, materials,
            chunk_samples=chunk_samples,
            node_title=node_title,
            chapter_profile=chapter_profile,
            max_total_chars=max_total_chars,
        )

    def _build_material_context_entries(
        self,
        materials: list[WritingMaterial],
        *,
        chunk_samples: Optional[dict[int, list[str]]] = None,
        node_title: str = "",
        chapter_profile: Optional[dict[str, Any]] = None,
    ) -> list[tuple[int, str, bool]]:
        return material_context.build_material_context_entries(
            self, materials,
            chunk_samples=chunk_samples,
            node_title=node_title,
            chapter_profile=chapter_profile,
        )

    def _build_material_context_entry(
        self,
        material: WritingMaterial,
        excerpts: Optional[list[str]] = None,
        node_title: str = "",
        chapter_profile: Optional[dict[str, Any]] = None,
    ) -> str:
        return material_context.build_material_context_entry(
            self, material, excerpts=excerpts,
            node_title=node_title,
            chapter_profile=chapter_profile,
        )

    def _build_material_usage_rules(self, materials: list[WritingMaterial]) -> str:
        return material_context.build_material_usage_rules(self, materials)

    def _progress(
        self,
        cb: Optional[ProgressCallback],
        stage: str, current: int, total: int, message: str,
    ) -> None:
        if cb:
            try:
                cb(stage, current, total, message)
            except Exception as e:
                # 与 ClauseExtractor._report_progress 对齐: 进度回调失败不阻断主流程，
                # 但必须打 warning 暴露回调链断裂，避免静默吞异常导致排障困难。
                logger.warning(f"进度回调失败: {e}")


def get_section_composer(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    project_id: Optional[int] = None,
) -> SectionComposer:
    """工厂函数"""
    return SectionComposer(db=db, llm_client=llm_client, project_id=project_id)
