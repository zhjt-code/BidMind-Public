"""
职责: 对招标分块批次各调用一次 LLM，从同一 JSON 拆出 hard/structure/guide。
不做: 不去重、不写库、不报 completed、不跑评分权威、不修改 ClauseExtractor 类。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional

from loguru import logger

from app.core.ai.prompt_registry import resolve_runtime_params
from app.core.writer.prompts.clause_extract_unified import (
    UNIFIED_CLAUSE_NODE_KEY,
    get_unified_clause_extract_prompt,
)
from app.schemas.writing import ExtractedClause

_COMPAT_RATIO_THRESHOLD = 0.02
_UNIFIED_EXTRACT_MAX_TOKENS = 6000
_PROGRESS_STAGE = "extracting_clauses"

PromptBuilder = Callable[[str, int, int], tuple[str, str]]
VerifyFn = Callable[[list[ExtractedClause], str, int, int], list[ExtractedClause]]
SectionContextFn = Callable[[Any, list[Any]], tuple[str, bool]]
QualityFn = Callable[[str, str], float]
ProgressFn = Callable[..., None]


@dataclass(frozen=True)
class ClauseLayerPassRequest:
    """单次过正文提取入参。"""

    batches: list[list[Any]]
    llm_client: Any
    concurrency: int
    verify_batch_clauses: VerifyFn
    all_chunks: Optional[list[Any]] = None
    report_progress: Optional[ProgressFn] = None
    detect_section_context: Optional[SectionContextFn] = None
    check_text_quality: Optional[QualityFn] = None
    build_prompt: Optional[PromptBuilder] = None


@dataclass(frozen=True)
class ClauseLayerPassResult:
    """按层拆好的提取结果，供后续同层去重消费。"""

    hard: list[ExtractedClause]
    structure: list[ExtractedClause]
    guide: list[ExtractedClause]


def _empty_result() -> ClauseLayerPassResult:
    return ClauseLayerPassResult(hard=[], structure=[], guide=[])


def _parse_items(raw_list: Any) -> list[ExtractedClause]:
    if not isinstance(raw_list, list):
        return []
    clauses: list[ExtractedClause] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        try:
            clauses.append(ExtractedClause(**item))
        except Exception as exc:
            logger.warning(f"单条约束解析失败: item={item}, err={exc}")
    return clauses


def _split_unified_payload(payload: Any) -> tuple[list[Any], list[Any], list[Any]] | None:
    if not isinstance(payload, dict):
        return None
    hard = payload.get("hard")
    if hard is None:
        hard = payload.get("hard_constraint")
    structure = payload.get("structure")
    if structure is None:
        structure = payload.get("structure_constraint")
    guide = payload.get("guide")
    if guide is None:
        guide = payload.get("generation_guide")
    if not isinstance(hard, list):
        hard = []
    if not isinstance(structure, list):
        structure = []
    if not isinstance(guide, list):
        guide = []
    return hard, structure, guide


def _push_batch_dropped_signal(batch: list[Any]) -> None:
    try:
        from app.core.contracts.degradation_signal import DegradationSignal, push_signal

        push_signal(DegradationSignal(
            source="clause_batch_extract_failed",
            flow="writer",
            repair_type="batch_dropped",
            original_length=sum(len(getattr(chunk, "content", None) or "") for chunk in batch),
            recovered_elements=0,
        ))
    except Exception:
        pass


async def _extract_one_batch(
    request: ClauseLayerPassRequest,
    batch: list[Any],
) -> ClauseLayerPassResult:
    content_parts: list[str] = []
    page_nums: list[int] = []
    all_chunks = request.all_chunks
    if all_chunks and batch and request.detect_section_context is not None:
        try:
            section, is_contract = request.detect_section_context(batch[0], all_chunks)
        except Exception as exc:
            logger.warning(f"章节上下文检测失败，降级为空 section: {exc}")
            section, is_contract = "", False
        if section:
            phase = "合同/履约阶段" if is_contract else "投标准备阶段"
            content_parts.append(f"[当前内容所属章节: {section}（{phase}）]")

    for chunk in batch:
        content_parts.append(getattr(chunk, "content", None) or "")
        page_num = getattr(chunk, "page_num", None)
        if page_num is not None:
            page_nums.append(page_num)

    content = "\n\n".join(content_parts)
    page_start = min(page_nums) if page_nums else 0
    page_end = max(page_nums) if page_nums else 0

    if request.check_text_quality is not None:
        ratio = request.check_text_quality(content, "writer")
        if ratio > _COMPAT_RATIO_THRESHOLD:
            logger.warning(
                f"Chunk 文本质量异常（CJK compat ratio={ratio:.3f} > "
                f"{_COMPAT_RATIO_THRESHOLD}），pages={page_start}-{page_end}，跳过 LLM 调用"
            )
            return _empty_result()

    build_prompt = request.build_prompt or get_unified_clause_extract_prompt
    system_msg, user_msg = build_prompt(content, page_start, page_end)
    temperature, max_tokens = resolve_runtime_params(
        UNIFIED_CLAUSE_NODE_KEY, 0.1, _UNIFIED_EXTRACT_MAX_TOKENS,
    )
    result = await request.llm_client.chat_json(
        user_message=user_msg,
        system_message=system_msg,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    split = _split_unified_payload(result)
    if split is None:
        logger.warning("统一提取返回非对象 JSON，本批次丢弃（避免误判所属层）")
        return _empty_result()

    hard_raw, structure_raw, guide_raw = split
    hard = request.verify_batch_clauses(_parse_items(hard_raw), content, page_start, page_end)
    structure = request.verify_batch_clauses(
        _parse_items(structure_raw), content, page_start, page_end
    )
    guide = request.verify_batch_clauses(_parse_items(guide_raw), content, page_start, page_end)
    return ClauseLayerPassResult(hard=hard, structure=structure, guide=guide)


async def extract_clause_layers_from_batches(
    request: ClauseLayerPassRequest,
) -> ClauseLayerPassResult:
    """对全部批次并行提取，每批一次 LLM，三层从同一次响应拆出。"""
    batches = request.batches
    total_batches = len(batches)
    if total_batches <= 0:
        return _empty_result()

    semaphore = asyncio.Semaphore(max(int(request.concurrency), 1))
    extracted_count = 0
    progress_lock = asyncio.Lock()

    async def extract_batch(batch: list[Any]) -> ClauseLayerPassResult:
        nonlocal extracted_count
        async with semaphore:
            try:
                result = await _extract_one_batch(request, batch)
            except (KeyError, IndexError):
                async with progress_lock:
                    extracted_count += 1
                logger.error("统一提取 Prompt 格式化失败，终止约束提取任务")
                raise
            except Exception as exc:
                logger.warning(f"批次提取失败: {exc}")
                _push_batch_dropped_signal(batch)
                result = _empty_result()
            async with progress_lock:
                extracted_count += 1
                current = extracted_count
            if request.report_progress is not None:
                request.report_progress(
                    _PROGRESS_STAGE,
                    current,
                    total_batches,
                    f"约束提取 {current}/{total_batches}",
                )
            return result

    results = await asyncio.gather(*[extract_batch(batch) for batch in batches])
    hard: list[ExtractedClause] = []
    structure: list[ExtractedClause] = []
    guide: list[ExtractedClause] = []
    for batch_result in results:
        hard.extend(batch_result.hard)
        structure.extend(batch_result.structure)
        guide.extend(batch_result.guide)
    return ClauseLayerPassResult(hard=hard, structure=structure, guide=guide)
