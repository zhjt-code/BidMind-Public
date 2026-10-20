"""
职责: 对高风险作用域页窗调用 LLM，抽出原子废标/无效投标条件。
不做: 不定位置信度、不落库、不跑评分直传通道。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

from loguru import logger

from app.core.ai.prompts.mandatory_atomic import get_mandatory_atomic_prompt
from app.core.config import get_review_concurrency, settings
from app.core.extractor.mandatory.contracts import AtomicCondition, PageText, ScopeWindow
from app.core.extractor.mandatory.requirement_voice import (
    COVER_RATIO_THRESHOLD,
    bigram_hit_ratio,
)
from app.core.extractor.tender_extractor_parts.category_inference import (
    infer_requirement_category,
)

_VALID_CATEGORIES = frozenset({"QUAL", "TECH", "COMM", "PERF", "STAFF", "FORMAT"})


async def extract_atomic_conditions(
    llm_client: Any,
    *,
    pages: Sequence[PageText],
    windows: Sequence[ScopeWindow],
    hints: Sequence[str] = (),
    progress_callback: Callable[..., None] | None = None,
) -> list[AtomicCondition]:
    """按页窗拼接原文后抽取原子条件；LLM 失败返回空列表。"""
    if llm_client is None or not windows:
        return []
    page_map = {page.page_num: page for page in pages}
    budget = max(2000, int(getattr(settings, "TENDER_MANDATORY_COVERAGE_MAX_CHARS", 12000)))
    collected: list[AtomicCondition] = []
    hint_text = "\n".join(f"- {item}" for item in hints if item) or "（无）"
    total = len(windows)
    concurrency = max(1, int(get_review_concurrency() or 1))
    sem = asyncio.Semaphore(concurrency)
    done = 0
    done_lock = asyncio.Lock()
    _emit_window_progress(progress_callback, 0, total)

    async def _one_window(index: int, window: ScopeWindow) -> list[AtomicCondition]:
        payload = _window_payload(window, page_map, budget)
        if not payload.strip():
            return []
        try:
            system_msg, user_msg = get_mandatory_atomic_prompt(payload, hint_text)
            response = await llm_client.chat_json(
                user_message=user_msg,
                system_message=system_msg,
                temperature=0.0,
                max_tokens=4000,
            )
        except Exception as exc:
            logger.warning(
                "高风险原子条件抽取失败 window={}/{} pages={}: {}",
                index,
                total,
                window.pages,
                exc,
            )
            return []
        logger.info(
            "高风险原子条件抽取完成 window={}/{} pages={}",
            index,
            total,
            window.pages,
        )
        return _parse_atomics(response, fallback_pages=window.pages, page_map=page_map)

    async def _guarded(index: int, window: ScopeWindow) -> list[AtomicCondition]:
        nonlocal done
        async with sem:
            result = await _one_window(index, window)
        async with done_lock:
            done += 1
            current = done
        _emit_window_progress(progress_callback, current, total)
        return result

    batches = await asyncio.gather(
        *(_guarded(index, window) for index, window in enumerate(windows, start=1))
    )
    for batch in batches:
        collected.extend(batch)
    return _dedupe_atomics(collected)


def _emit_window_progress(
    progress_callback: Callable[..., None] | None,
    current: int,
    total: int,
) -> None:
    """current 为已完成窗数（含开工时的 0），不是正在跑的窗口序号。"""
    if progress_callback is None:
        return
    message = f"核验否决与资格条款 {current}/{total}"
    try:
        progress_callback("mandatory_coverage", current, total, message)
    except TypeError:
        progress_callback("mandatory_coverage", current, total, message, None)
    except Exception as exc:
        logger.warning("高风险核验窗口进度回调失败: {}", exc)


def _window_payload(
    window: ScopeWindow,
    page_map: dict[int, PageText],
    budget: int,
) -> str:
    parts: list[str] = []
    used = 0
    for page_num in window.pages:
        page = page_map.get(page_num)
        if page is None or not page.text.strip():
            continue
        block = f"[页 {page_num}]\n{page.text.strip()}"
        if used and used + len(block) > budget:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def _parse_atomics(
    response: Any,
    fallback_pages: tuple[int, ...],
    page_map: dict[int, PageText] | None = None,
) -> list[AtomicCondition]:
    raw_items: list[Any]
    if isinstance(response, list):
        raw_items = response
    elif isinstance(response, dict):
        raw_items = response.get("items") or response.get("requirements") or []
    else:
        return []
    parsed: list[AtomicCondition] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        if len(description) < 8:
            continue
        category = str(item.get("category") or "").upper()
        if category not in _VALID_CATEGORIES:
            category = infer_requirement_category(description) or "QUAL"
        pages = anchor_pages_to_text(
            description,
            _coerce_pages(item.get("source_pages"), fallback_pages),
            fallback_pages,
            page_map or {},
        )
        keywords = tuple(
            str(token).strip()
            for token in (item.get("keywords") or [])
            if str(token).strip()
        )
        parsed.append(
            AtomicCondition(
                description=description,
                category=category,
                source_pages=pages,
                keywords=keywords,
            )
        )
    return parsed


def anchor_pages_to_text(
    description: str,
    claimed: tuple[int, ...],
    window_pages: tuple[int, ...],
    page_map: dict[int, PageText],
) -> tuple[int, ...]:
    """按窗口原文命中回填页码，不信任模型自报页码。

 多页拼接送模型时，模型常把 P13 的条件标成 P6；补充信号随每个窗口下发，模型
 也可能在不含该原文的窗口里产出它。与普通通道 `align_chunk_source_pages` 同一
 原则：页码来自原文命中，而非模型声明。先在窗内找，再全文找；都无命中时保留
 模型页码（若在窗内），否则退回窗口页。
 """
    if not page_map or not window_pages:
        return claimed
    for candidate_pages in (window_pages, tuple(sorted(page_map))):
        scored = [
            (bigram_hit_ratio(page_map[p].text, description), p)
            for p in candidate_pages
            if p in page_map
        ]
        anchored = [p for ratio, p in sorted(scored, reverse=True) if ratio >= COVER_RATIO_THRESHOLD]
        if anchored:
            confirmed = tuple(p for p in claimed if p in anchored)
            return confirmed or (anchored[0],)
    in_window = tuple(p for p in claimed if p in window_pages)
    return in_window or window_pages


def _coerce_pages(value: Any, fallback: tuple[int, ...]) -> tuple[int, ...]:
    pages: list[int] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, int):
                pages.append(item)
            elif isinstance(item, str) and item.isdigit():
                pages.append(int(item))
    return tuple(pages) if pages else fallback


def _dedupe_atomics(items: list[AtomicCondition]) -> list[AtomicCondition]:
    from app.core.extractor.tender_extractor_parts.dedup_normalize import normalize_dedup_key

    seen: set[str] = set()
    unique: list[AtomicCondition] = []
    for item in items:
        key = normalize_dedup_key(item.description)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
