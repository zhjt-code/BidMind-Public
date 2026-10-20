"""TenderExtractor 单分块提取阶段（R2-2 拆分产出）。

承接原 `tender_extractor.py` 中以下 8 个方法：
- `_normalize_raw_item`            LLM 单条 dict 归一化（category 别名映射）
- `_infer_category_from_content`   关键词推断 category（低置信返回 None）
- `_summarize_invalid_raw_item`    salvage 失败条目的用户友好摘要
- `_parse_items_with_salvage`      逐条 Pydantic 校验 + 失败摘要收集
- `_merge_provenance`              来源页码/关键词/子要求并入（去重不丢源）
- `_align_chunk_source_pages`      source_pages 折回 chunk.page_num（防 LLM 幻觉）
- `_extract_from_chunk`            单分块 LLM 提取主流程（含重试 + salvage + 对齐）
- `_local_pre_dedupe`              LLM 合并前的同类别快速预去重

设计：函数第一参数 `self` 保留，函数名相对原 method **去前导下划线**，
body 与原 method **字面一致**。模块级 `get_tender_extract_prompt`
直接 import（与原文件一致，兼容 test_tender_extractor_*.py 的 sys.modules
假模块注入）。

注：`_extract_from_chunk` 的 `@_observability_instrument` 装饰器**保留在
主类 thin forwarder**，本模块函数为无装饰器纯函数（避免 observability 二次包装）。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import ValidationError

from app.core.ai.llm_errors import EmptyLLMResponseError
from app.core.ai.prompts.tender_extract import get_tender_extract_prompt
from app.core.config import settings
from app.core.contracts.degradation_signal import DegradationSignal
from app.core.extractor.relevance_resolver import resolve_review_relevance
from app.core.extractor.section_context import classify_section_phase
from app.core.extractor.tender_extractor_parts import (
    category_inference,
    chunk_extraction_cache,
)
from app.core.extractor.tender_extractor_parts.extraction_units import (
    prepend_lead_context,
)
from app.core.extractor.tender_extractor_parts.protected_premerge import (
    compress_low_risk_duplicate_requirements,
)
from app.models.chunk import Chunk
from app.schemas.requirement import ExtractedRequirement

from .chunk_budget_policy import (
    CHUNK_EXTRACT_TOKEN_CEILING,
    ChunkJsonErrorKind,
    classify_chunk_extract_json_error,
    escalate_chunk_max_tokens,
    initial_chunk_max_tokens,
    pop_lossy_repair_signals,
)

if TYPE_CHECKING:
    from app.core.extractor.tender_extractor import TenderExtractor


_REVIEW_RELEVANCE_CATEGORY_SPILL_VALUES = frozenset(
    {"ACTIONABLE", "INFORMATIONAL", "PROCEDURAL"}
)
_VALID_REVIEW_RELEVANCE_VALUES = frozenset(
    {"actionable", "informational", "procedural"}
)


@dataclass
class _ChunkPromptContext:
    system_msg: str
    user_msg: str
    is_contract_section: bool


@dataclass
class _ChunkAttemptState:
    current_max_tokens: int
    best_partial: list[ExtractedRequirement] | None = None
    best_partial_reason: str | None = None
    last_error: Exception | None = None


def _apply_review_relevance(
    items: list[ExtractedRequirement],
    *,
    is_contract_section: bool,
) -> list[ExtractedRequirement]:
    """对单 chunk 抽取结果做评审意义"降级"兜底（P0/P1）。

    职责: 合同区段整段降为 informational + 噪音规则护栏降级，仅 actionable -> 其他。
    不做: 不升级为 actionable、不改 description/category、不调用 LLM。
    """
    if not settings.TENDER_REVIEW_RELEVANCE_ENABLED:
        return items
    refined: list[ExtractedRequirement] = []
    for item in items:
        relevance = resolve_review_relevance(
            item.review_relevance,
            item.description,
            item.keywords,
            category=item.category,
            is_mandatory=bool(item.is_mandatory),
            is_contract_section=is_contract_section,
        )
        if relevance != item.review_relevance:
            item = item.model_copy(update={"review_relevance": relevance})
        refined.append(item)
    return refined


def normalize_raw_item(self: "TenderExtractor", item: dict) -> dict:
    """对 LLM 返回的单条原始 dict 做低风险归一化。

    仅处理已知安全映射，不做高风险默认推断。
    """
    if not isinstance(item, dict):
        return item

    raw_cat = str(item.get("category", "")).strip().upper()
    if raw_cat not in self._VALID_CATEGORIES:
        if raw_cat in _REVIEW_RELEVANCE_CATEGORY_SPILL_VALUES:
            original_category = item.get("category")
            current_relevance = item.get("review_relevance")
            if current_relevance not in _VALID_REVIEW_RELEVANCE_VALUES:
                item["review_relevance"] = raw_cat.lower()
            inferred = self._infer_category_from_content(item)
            if inferred:
                item["category"] = inferred
                logger.info(
                    "category 字段收到评审意义值 '{}', 已迁移并推断分类为 '{}'",
                    original_category,
                    inferred,
                )
            else:
                logger.warning(
                    "category 字段收到评审意义值 '{}', 已迁移评审意义但未能推断分类",
                    original_category,
                )
        else:
            mapped = self._CATEGORY_ALIAS_MAP.get(raw_cat)
            if mapped:
                logger.info(
                    "category 归一化: '{}' -> '{}' (chunk 预处理)",
                    item.get("category"),
                    mapped,
                )
                item["category"] = mapped
            elif raw_cat:
                logger.warning(
                    "category 未知值 '{}', 尝试基于关键词推断",
                    item.get("category"),
                )
                inferred = self._infer_category_from_content(item)
                if inferred:
                    item["category"] = inferred

    for field in ("topic", "subject_scope", "canonical_key"):
        if field in item and item[field] is None:
            del item[field]

    return item


def infer_category_from_content(self: "TenderExtractor", item: dict) -> str | None:
    """根据 description + keywords 推断 category，低置信时返回 None。"""
    inferred = category_inference.infer_requirement_category(
        item.get("description"),
        item.get("keywords"),
    )
    if inferred:
        logger.info(
            "category 基于关键词推断: '{}' -> '{}' (content hint)",
            item.get("category"),
            inferred,
        )
    return inferred


def summarize_invalid_raw_item(
    self: "TenderExtractor",
    raw: dict,
    normalized: dict,
    item_idx: int,
    item_err: ValidationError,
) -> dict[str, Any]:
    """把被丢弃的原始条目压缩成可展示给用户核对的摘要。"""
    candidate_text = normalized.get("description") or raw.get("description")
    if not isinstance(candidate_text, str) or not candidate_text.strip():
        keywords = raw.get("keywords") or normalized.get("keywords") or []
        if isinstance(keywords, list):
            candidate_text = "、".join(str(k) for k in keywords if str(k).strip())
    if not isinstance(candidate_text, str) or not candidate_text.strip():
        candidate_text = f"第 {item_idx + 1} 个自动提取条目"

    summary = re.sub(r"\s+", " ", candidate_text).strip()
    if len(summary) > 80:
        summary = summary[:77] + "..."

    field_names = {
        str(err.get("loc", ("",))[0])
        for err in item_err.errors()
        if err.get("loc")
    }
    if "description" in field_names:
        reason = "要求内容为空或过短"
    elif "category" in field_names:
        reason = "要求分类无法识别"
    elif "is_mandatory" in field_names:
        reason = "未能判断是否为否决项"
    elif "keywords" in field_names or "source_pages" in field_names:
        reason = "关键词或来源页码格式不完整"
    else:
        reason = "字段不完整"

    return {
        "item_idx": item_idx,
        "summary": summary,
        "reason": reason,
    }


def parse_items_with_salvage(
    self: "TenderExtractor",
    items: list[dict],
    chunk_id: int | None = None,
) -> tuple[list[ExtractedRequirement], list[dict[str, Any]]]:
    """逐条校验 + 局部 salvage。

    Returns:
        (合法条目列表, 失败条目摘要列表)
    """
    valid: list[ExtractedRequirement] = []
    failed_items: list[dict[str, Any]] = []
    for idx, raw in enumerate(items):
        normalized = self._normalize_raw_item(dict(raw))
        try:
            valid.append(ExtractedRequirement(**normalized))
        except ValidationError as item_err:
            failed_items.append(
                self._summarize_invalid_raw_item(raw, normalized, idx, item_err)
            )
            logger.warning(
                "条目校验失败 (chunk={}, item_idx={}, error={})",
                chunk_id,
                idx,
                str(item_err)[:200],
            )
    return valid, failed_items


def merge_provenance(
    self: "TenderExtractor",
    into: ExtractedRequirement,
    extra: ExtractedRequirement,
) -> ExtractedRequirement:
    """将 ``extra`` 的来源页码 / 关键词 / 子要求 / 合并计数回并到 ``into``。"""
    merged_pages: list[int] = []
    seen_pages: set[int] = set()
    for raw in [*(into.source_pages or []), *(extra.source_pages or [])]:
        if isinstance(raw, int) and raw >= 1 and raw not in seen_pages:
            seen_pages.add(raw)
            merged_pages.append(raw)
    merged_pages.sort()

    merged_keywords: list[str] = []
    seen_kw: set[str] = set()
    for kw in [*(into.keywords or []), *(extra.keywords or [])]:
        if not isinstance(kw, str):
            continue
        key = kw.strip().lower()
        if not key or key in seen_kw:
            continue
        seen_kw.add(key)
        merged_keywords.append(kw)

    merged_sub: list[str] = []
    seen_sub: set[str] = set()
    for sub in [*(into.sub_requirements or []), *(extra.sub_requirements or [])]:
        if not isinstance(sub, str):
            continue
        key = sub.strip()
        if not key or key in seen_sub:
            continue
        seen_sub.add(key)
        merged_sub.append(sub)

    new_count = (into.merged_count or 1) + (extra.merged_count or 1)
    return into.model_copy(
        update={
            "source_pages": merged_pages,
            "keywords": merged_keywords,
            "sub_requirements": merged_sub,
            "merged_count": new_count,
        }
    )


def align_chunk_source_pages(
    self: "TenderExtractor",
    items: list[ExtractedRequirement],
    chunk: Chunk,
) -> list[ExtractedRequirement]:
    """将单 chunk 抽取的条目 source_pages 校正到真实 chunk.page_num。"""
    page = chunk.page_num
    if page is None or page < 1:
        return items
    aligned: list[ExtractedRequirement] = []
    for item in items:
        raw_pages = list(item.source_pages or [])
        unsafe = [p for p in raw_pages if not isinstance(p, int) or p != page]
        if unsafe or not raw_pages:
            if unsafe:
                logger.debug(
                    "chunk source_pages 校正: chunk_id={} chunk_page={} llm_pages={} -> [{}]",
                    chunk.id,
                    page,
                    raw_pages,
                    page,
                )
            item = item.model_copy(update={"source_pages": [page]})
        aligned.append(item)
    return aligned


async def _save_chunk_cache_guarded(
    self: "TenderExtractor",
    cache_key: chunk_extraction_cache.ChunkExtractionCacheKey,
    items: list[ExtractedRequirement],
    *,
    empty_result: bool,
) -> None:
    """保存单 chunk 提取缓存，统一处理可选锁。"""
    cache_lock = getattr(self, "_chunk_cache_lock", None)
    if cache_lock is not None:
        async with cache_lock:
            await chunk_extraction_cache.save_chunk_requirements_cache(
                self.db, cache_key, items, empty_result=empty_result
            )
    else:
        await chunk_extraction_cache.save_chunk_requirements_cache(
            self.db, cache_key, items, empty_result=empty_result
        )


def _record_chunk_partial_recovery(
    self: "TenderExtractor",
    chunk: Chunk,
    items: list[ExtractedRequirement],
    *,
    reason: str,
    max_tokens: int,
) -> None:
    """记录有损 JSON 修复产物被采纳但禁止缓存的事实。"""
    self._record_stage_failure(
        "chunk_partial_recovery",
        f"分块 {chunk.id} 采用有损JSON修复结果，跳过缓存写入",
        chunk_id=chunk.id,
        page_num=chunk.page_num,
        recovered_count=len(items),
        repair_reason=reason,
        max_tokens=max_tokens,
    )


def _record_chunk_extraction_failure(
    self: "TenderExtractor",
    chunk: Chunk,
    last_error: Exception | None,
) -> None:
    """记录分块提取重试耗尽失败。"""
    logger.error(
        f"分块 {chunk.id} 提取失败，超过最大重试次数 "
        f"(page={chunk.page_num}, last_error={last_error})"
    )
    self._record_stage_failure(
        "chunk_extraction",
        f"分块 {chunk.id} 提取失败，超过最大重试次数",
        chunk_id=chunk.id,
        page_num=chunk.page_num,
        last_error=str(last_error),
    )


def _return_best_partial_if_present(
    self: "TenderExtractor",
    chunk: Chunk,
    state: _ChunkAttemptState,
    *,
    fallback_reason: str,
) -> list[ExtractedRequirement] | None:
    """若已有有损修复结果，则记录并返回该部分结果。"""
    if state.best_partial is None:
        return None
    _record_chunk_partial_recovery(
        self,
        chunk,
        state.best_partial,
        reason=state.best_partial_reason or fallback_reason,
        max_tokens=state.current_max_tokens,
    )
    return state.best_partial


def _prepare_chunk_prompt(
    self: "TenderExtractor",
    chunk: Chunk,
    retry_mode: str | None,
) -> _ChunkPromptContext:
    """构造单分块提取 prompt，并注入章节上下文与上文承接。"""
    chunk_content = prepend_lead_context(
        chunk.content or "",
        (getattr(self, "_chunk_lead_context", None) or {}).get(chunk.id, ""),
    )
    is_contract_section = False
    if settings.TENDER_EXTRACT_SECTION_CONTEXT_ENABLED:
        phase = classify_section_phase(chunk.section_path, chunk_content)
        is_contract_section = phase.is_contract
        section_title = (chunk.section_path or "").strip()
        if section_title:
            phase_label = "合同/履约阶段" if is_contract_section else "投标准备阶段"
            chunk_content = (
                f"[当前内容所属章节: {section_title}（{phase_label}）]\n\n{chunk_content}"
            )

    system_msg, user_msg = get_tender_extract_prompt(
        content=chunk_content,
        page_num=chunk.page_num or 0,
    )
    if retry_mode == "validation_rescue":
        user_msg += self._VALIDATION_CORRECTION_HINT
    return _ChunkPromptContext(system_msg, user_msg, is_contract_section)


async def _load_chunk_cache_if_enabled(
    self: "TenderExtractor",
    chunk: Chunk,
    prompt_ctx: _ChunkPromptContext,
    retry_mode: str | None,
) -> tuple[chunk_extraction_cache.ChunkExtractionCacheKey | None, list[ExtractedRequirement] | None]:
    """按配置读取单 chunk 提取缓存。"""
    cache_enabled = (
        retry_mode is None
        and settings.TENDER_CHUNK_EXTRACT_CACHE_ENABLED
    )
    if not cache_enabled:
        return None, None

    cache_key = chunk_extraction_cache.build_chunk_extraction_cache_key(
        chunk=chunk,
        system_msg=prompt_ctx.system_msg,
        user_msg=prompt_ctx.user_msg,
        model_id=chunk_extraction_cache.resolve_chunk_cache_model_id(
            self.llm_client
        ),
        schema_version=settings.TENDER_CHUNK_EXTRACT_CACHE_SCHEMA_VERSION,
        section_context_enabled=settings.TENDER_EXTRACT_SECTION_CONTEXT_ENABLED,
    )
    cache_lock = getattr(self, "_chunk_cache_lock", None)
    if cache_lock is not None:
        async with cache_lock:
            cached = await chunk_extraction_cache.load_cached_chunk_requirements(
                self.db, cache_key
            )
    else:
        cached = await chunk_extraction_cache.load_cached_chunk_requirements(
            self.db, cache_key
        )
    return cache_key, cached


def _raw_items_from_chunk_response(response: Any) -> list[dict]:
    """从 LLM 响应中提取原始 requirements 列表。"""
    if isinstance(response, list):
        return response
    if isinstance(response, dict) and "requirements" in response:
        return response["requirements"]
    logger.warning(f"未知的响应格式: {type(response)}")
    return []


def _record_chunk_item_failures(
    self: "TenderExtractor",
    chunk: Chunk,
    valid: list[ExtractedRequirement],
    failed_items: list[dict],
) -> None:
    """记录逐条 salvage 中被丢弃的非法条目。"""
    item_failures = len(failed_items)
    if item_failures <= 0:
        return
    logger.info(
        "分块 {} 逐条 salvage: {} 条合法, {} 条失败",
        chunk.id, len(valid), item_failures,
    )
    self._record_stage_failure(
        "chunk_item_validation",
        f"分块 {chunk.id} 有 {item_failures} 条校验失败被丢弃",
        chunk_id=chunk.id,
        page_num=chunk.page_num,
        valid_count=len(valid),
        failed_count=item_failures,
        failed_items=[
            {**item, "page_num": chunk.page_num}
            for item in failed_items
        ],
    )


def _prepare_valid_chunk_items(
    self: "TenderExtractor",
    chunk: Chunk,
    valid: list[ExtractedRequirement],
    *,
    is_contract_section: bool,
) -> list[ExtractedRequirement]:
    """对合法条目做页码对齐与评审意义降级。"""
    valid = self._align_chunk_source_pages(valid, chunk)
    return _apply_review_relevance(
        valid, is_contract_section=is_contract_section
    )


def _build_truncation_retry_user_msg(base_user_msg: str, *, partial: bool) -> str:
    """构造截断重试提示。"""
    if partial:
        return (
            base_user_msg
            + "\n\n你的上次输出疑似被截断，只保留了部分JSON。"
              "请精简每条要求描述，确保输出完整JSON数组。"
        )
    return (
        base_user_msg
        + "\n\n你的上次输出被截断导致JSON不完整。"
          "请精简每条要求描述，确保输出完整 JSON 数组。"
    )


async def _call_chunk_llm(
    self: "TenderExtractor",
    *,
    user_msg: str,
    system_msg: str,
    temperature: float,
    max_tokens: int,
) -> tuple[Any, list[DegradationSignal]]:
    """执行一次 LLM 调用并返回有损修复信号。"""
    metrics = getattr(self, "_extraction_metrics", None)
    if metrics is not None:
        metrics.record_llm_call()
    pop_lossy_repair_signals()
    response = await self.llm_client.chat_json(
        user_message=user_msg,
        system_message=system_msg,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response, pop_lossy_repair_signals()


async def extract_from_chunk(
    self: "TenderExtractor",
    chunk: Chunk,
    *,
    retry_mode: str | None = None,
) -> list[ExtractedRequirement] | None:
    """从单个分块中提取要求。"""
    prompt_ctx = _prepare_chunk_prompt(self, chunk, retry_mode)
    cache_key, cached = await _load_chunk_cache_if_enabled(
        self, chunk, prompt_ctx, retry_mode
    )
    if cached is not None:
        metrics = getattr(self, "_extraction_metrics", None)
        if metrics is not None:
            metrics.record_cache_hit(item_count=len(cached))
        return cached

    base_user_msg = prompt_ctx.user_msg
    user_msg = prompt_ctx.user_msg
    state = _ChunkAttemptState(
        current_max_tokens=initial_chunk_max_tokens(retry_mode)
    )
    for attempt in range(self.max_retries):
        if attempt > 0:
            await asyncio.sleep(2 ** attempt)

        base_temp = 0.05 if retry_mode == "validation_rescue" else 0.1
        temperature = base_temp if attempt == 0 else 0.0

        try:
            response, lossy_signals = await _call_chunk_llm(
                self,
                user_msg=user_msg,
                system_msg=prompt_ctx.system_msg,
                temperature=temperature,
                max_tokens=state.current_max_tokens,
            )
            raw_items = _raw_items_from_chunk_response(response)

            if not raw_items:
                partial = _return_best_partial_if_present(
                    self, chunk, state, fallback_reason="empty_after_partial"
                )
                if partial is not None:
                    return partial
                if cache_key is not None:
                    await _save_chunk_cache_guarded(
                        self, cache_key, [], empty_result=True
                    )
                return []

            valid, failed_items = self._parse_items_with_salvage(
                raw_items, chunk_id=chunk.id
            )
            _record_chunk_item_failures(self, chunk, valid, failed_items)

            if valid:
                valid = _prepare_valid_chunk_items(
                    self,
                    chunk,
                    valid,
                    is_contract_section=prompt_ctx.is_contract_section,
                )
                if lossy_signals:
                    state.best_partial = valid
                    state.best_partial_reason = ",".join(
                        signal.repair_type for signal in lossy_signals
                    )
                    can_retry = attempt < self.max_retries - 1
                    next_tokens = escalate_chunk_max_tokens(state.current_max_tokens)
                    if can_retry and next_tokens > state.current_max_tokens:
                        logger.info(
                            "分块 {} 有损JSON修复后触发扩容重试: "
                            "max_tokens {}->{}, repair_types={}",
                            chunk.id,
                            state.current_max_tokens,
                            next_tokens,
                            state.best_partial_reason,
                        )
                        state.current_max_tokens = next_tokens
                        user_msg = _build_truncation_retry_user_msg(
                            base_user_msg, partial=True
                        )
                        continue
                    _record_chunk_partial_recovery(
                        self,
                        chunk,
                        valid,
                        reason=state.best_partial_reason,
                        max_tokens=state.current_max_tokens,
                    )
                    return valid
                if cache_key is not None:
                    await _save_chunk_cache_guarded(
                        self, cache_key, valid, empty_result=False
                    )
                return valid

            state.last_error = ValidationError.from_exception_data(
                title="ExtractedRequirement",
                line_errors=[],
            )
            user_msg = base_user_msg + self._VALIDATION_CORRECTION_HINT
            continue

        except EmptyLLMResponseError as e:
            # 空响应由 chat_json 显式抛出；dispatcher 已在单次调用内
            # 换通道。此处不追加「请输出 JSON」提示（对空 content 无效）。
            state.last_error = e
            logger.warning(
                f"分块 {chunk.id} LLM空响应 "
                f"(attempt {attempt + 1}/{self.max_retries}): {e}"
            )
            continue

        except json.JSONDecodeError as e:
            state.last_error = e
            logger.warning(
                f"JSON 解析失败 (attempt {attempt + 1}/{self.max_retries}): {e}"
            )
            error_kind = classify_chunk_extract_json_error(e)
            if (
                error_kind is ChunkJsonErrorKind.TRUNCATION
                and state.current_max_tokens < CHUNK_EXTRACT_TOKEN_CEILING
                and attempt < self.max_retries - 1
            ):
                next_tokens = escalate_chunk_max_tokens(state.current_max_tokens)
                logger.info(
                    "分块 {} JSON疑似截断扩容重试: max_tokens {}->{}, err={}",
                    chunk.id,
                    state.current_max_tokens,
                    next_tokens,
                    str(e)[:120],
                )
                state.current_max_tokens = next_tokens
                user_msg = _build_truncation_retry_user_msg(
                    base_user_msg, partial=False
                )
            elif (
                error_kind is ChunkJsonErrorKind.MALFORMED
                and attempt == 0
            ):
                # 仅对有正文的畸形 JSON 追加格式提示；EMPTY_RESPONSE 追加无效
                user_msg = (
                    base_user_msg
                    + "\n\n请确保输出为有效的 JSON 数组格式，不要包含任何非JSON内容。"
                )

        except ValidationError as e:
            state.last_error = e
            logger.warning(
                f"数据校验失败 (attempt {attempt + 1}/{self.max_retries}): {e}"
            )
            user_msg = base_user_msg + self._VALIDATION_CORRECTION_HINT

    partial = _return_best_partial_if_present(
        self, chunk, state, fallback_reason="retry_exhausted"
    )
    if partial is not None:
        return partial

    _record_chunk_extraction_failure(self, chunk, state.last_error)
    return None


def local_pre_dedupe(
    self: "TenderExtractor",
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    """LLM 合并前的本地快速预去重。"""
    if not settings.TENDER_PROTECTED_PREMERGE_ENABLED or len(requirements) <= 1:
        return requirements
    return compress_low_risk_duplicate_requirements(requirements)
