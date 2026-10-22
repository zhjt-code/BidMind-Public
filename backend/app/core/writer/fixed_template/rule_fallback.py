"""固定模板规则兜底通道。

职责: 在 LLM 主路径完全空白时, 从 SectionRange 内做受限规则切分,
 产出 source=rule_only / 低置信 FixedTemplateEntry, 触发下游 needs_review。
不做: 不扫全文 chunks, 不替代 LLM 主路径, 不维护任务快照, 不调用 LLM。

设计要点 (与 旧 boundary.py 的关键差异):
- 输入范围严格限定在 ranges 给出的 page_start..page_end 内, 禁止全文 markers 扫描
- 置信度封顶 0.55, 低于 FixedTemplateEntry.is_complete_candidate 阈值 0.75,
 下游 template_route 自动打 fixed_template_review_gate
- source 标记 "rule_only", binding_loader._is_acceptable_template_clause 走例外通道放行
- volume_type 直接继承自 SectionRange.volume_type, 不再用文本启发式
"""

from __future__ import annotations

import re
from typing import Any, Optional

from loguru import logger

from app.core.writer.fixed_template.features import (
    extract_field_slots,
    extract_signature_slots,
    has_table_hint,
    is_directory_like,
    is_generic_template_requirement,
)
from app.core.writer.fixed_template.models import FixedTemplateEntry
from app.core.writer.fixed_template.raw_text_cleaner import clean_template_raw_text
from app.core.writer.fixed_template.section_locator import SectionRange
from app.core.writer.fixed_template.titles import (
    KNOWN_TEMPLATE_KEYS,
    aliases_for_key,
    canonical_title,
    infer_fixed_template_key,
)

_RULE_FALLBACK_CONFIDENCE = 0.55
_MIN_TEMPLATE_CHARS = 50
_TITLE_LINE_MAX_LEN = 40


def build_rule_fallback_entries(
    chunks: list[Any],
    ranges: list[SectionRange],
    *,
    min_page: int,
    max_page: int,
) -> list[FixedTemplateEntry]:
    """在 SectionRange 内做受限规则切分, 产出低置信兜底 entry。

    返回值: 按 (page_start, template_key, volume_type) 升序排序的 entry 列表。
    """
    if not ranges or not chunks:
        return []
    pages_by_range = _pages_within_ranges(chunks, ranges, min_page=min_page, max_page=max_page)
    if not pages_by_range:
        return []
    entries: list[FixedTemplateEntry] = []
    for range_item, pages in pages_by_range:
        entries.extend(_extract_entries_from_range(range_item, pages))
    deduped = _dedupe_entries(entries)
    if deduped:
        logger.info(
            "fixed_template rule_fallback 命中: count={}, keys={}, volumes={}",
            len(deduped),
            sorted({entry.template_key for entry in deduped}),
            sorted({entry.volume_type or "" for entry in deduped}),
        )
    return deduped


def _pages_within_ranges(
    chunks: list[Any],
    ranges: list[SectionRange],
    *,
    min_page: int,
    max_page: int,
) -> list[tuple[SectionRange, list[tuple[int, str]]]]:
    """把 chunks 按 page 聚合, 并按 ranges 切片。

    SectionRange 之间的页码可能重叠或相邻; 此处不去重, 由 _dedupe_entries 收尾。
    """
    by_page: dict[int, list[tuple[int, str]]] = {}
    for chunk in chunks:
        page = getattr(chunk, "page_num", None)
        if page is None:
            continue
        page_int = int(page)
        if page_int < min_page or page_int > max_page:
            continue
        content = str(getattr(chunk, "content", "") or "").strip()
        if not content:
            continue
        by_page.setdefault(page_int, []).append(
            (int(getattr(chunk, "chunk_index", 0) or 0), content),
        )
    if not by_page:
        return []
    result: list[tuple[SectionRange, list[tuple[int, str]]]] = []
    for range_item in ranges:
        pages: list[tuple[int, str]] = []
        for page in range(range_item.page_start, range_item.page_end + 1):
            items = by_page.get(page)
            if not items:
                continue
            text = "\n".join(
                content for _idx, content in sorted(items, key=lambda x: x[0])
            )
            if text.strip():
                pages.append((page, text))
        if pages:
            result.append((range_item, pages))
    return result


def _extract_entries_from_range(
    range_item: SectionRange,
    pages: list[tuple[int, str]],
) -> list[FixedTemplateEntry]:
    """在单个 SectionRange 内: 扫标题 markers + next-marker 切分 + 包装 entry。"""
    markers = _collect_markers(pages)
    if not markers:
        return []
    entries: list[FixedTemplateEntry] = []
    for pos, marker in enumerate(markers):
        page, line_index, title, template_key = marker
        next_marker = markers[pos + 1] if pos + 1 < len(markers) else None
        raw_text = _collect_segment_text(pages, page, line_index, next_marker)
        if not raw_text.strip():
            continue
        end_page = next_marker[0] if next_marker and next_marker[0] != page else page
        if end_page < page:
            end_page = page
        entry = _build_entry(
            template_key=template_key,
            title=title,
            page_start=page,
            page_end=min(end_page, range_item.page_end),
            raw_text=raw_text,
            volume_type=range_item.volume_type,
        )
        if entry is not None:
            entries.append(entry)
    return entries


def _collect_markers(
    pages: list[tuple[int, str]],
) -> list[tuple[int, int, str, str]]:
    """收集 range 内所有命中标题词典的 (page, line_index, title, template_key) markers。: chunk 化时 merge_soft_linebreaks 会把模板标题行与后续正文粘连成
 超长行(如 ``一、投标函致:中国石油化工…``), 旧实现按行长直接跳过导致该卷
 模板整体漏识别。对超长行补充"编号前缀 + 已知模板标题前缀"判定。
 """
    markers: list[tuple[int, int, str, str]] = []
    for page, text in pages:
        lines = _split_lines(text)
        for index, line in enumerate(lines):
            if len(line) > _TITLE_LINE_MAX_LEN:
                title = _extract_glued_title(line)
            else:
                title = _extract_template_title(line)
            if not title:
                continue
            key = infer_fixed_template_key(title)
            if key and key in KNOWN_TEMPLATE_KEYS:
                markers.append((page, index, title, key))
    return markers


def _split_lines(text: str) -> list[str]:
    raw_lines = re.split(r"[\r\n]+", text or "")
    lines: list[str] = []
    for raw in raw_lines:
        cleaned = re.sub(r"\s+", " ", raw).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _extract_template_title(line: str) -> Optional[str]:
    """从行中提取模板标题文本: 必须能映射到 KNOWN_TEMPLATE_KEYS 才返回。"""
    compact = re.sub(r"\s+", "", line or "")
    if "目录" in compact and len(compact) <= 12:
        return None
    # 行前可有"一、/二、/1./第X章"等编号, 后面跟模板标题
    match = re.match(
        r"^(?:第?[一二三四五六七八九十0-9]+[、.．章节]\s*)?(.{2,30})$",
        compact,
    )
    candidate = match.group(1) if match else compact
    inferred = infer_fixed_template_key(candidate)
    if inferred:
        return candidate
    return None


_GLUED_NUM_PREFIX_RE = re.compile(r"^第?[一二三四五六七八九十0-9]+[、.．]")


def _extract_glued_title(line: str) -> Optional[str]:
    """从粘连超长行中提取行首模板标题。

    仅当行以"编号 + 已知模板标题别名"开头时命中, 返回最长命中别名,
    避免把正文中顺带提及的模板名误判为章节起点。
    """
    compact = re.sub(r"\s+", "", line or "")
    prefix_match = _GLUED_NUM_PREFIX_RE.match(compact)
    if not prefix_match:
        return None
    rest = compact[prefix_match.end():]
    best: Optional[str] = None
    for template_key in KNOWN_TEMPLATE_KEYS:
        for alias in aliases_for_key(template_key):
            normalized = re.sub(r"\s+", "", alias)
            if rest.startswith(normalized) and (best is None or len(normalized) > len(best)):
                best = normalized
    return best


def _collect_segment_text(
    pages: list[tuple[int, str]],
    start_page: int,
    start_line_index: int,
    next_marker: Optional[tuple[int, int, str, str]],
) -> str:
    """在当前 range 内收集 [marker, next_marker) 之间的文本。"""
    collected: list[str] = []
    for page, text in pages:
        if page < start_page:
            continue
        lines = _split_lines(text)
        if next_marker and page > next_marker[0]:
            break
        for index, line in enumerate(lines):
            if page == start_page and index < start_line_index:
                continue
            if next_marker and page == next_marker[0] and index >= next_marker[1]:
                return "\n".join(collected).strip()
            collected.append(line)
    return "\n".join(collected).strip()


def _build_entry(
    *,
    template_key: str,
    title: str,
    page_start: int,
    page_end: int,
    raw_text: str,
    volume_type: Optional[str],
) -> Optional[FixedTemplateEntry]:
    """构造 FixedTemplateEntry, 应用与 post_validator 一致的最小过滤。"""
    if is_directory_like(raw_text) or is_generic_template_requirement(raw_text):
        return None
    try:
        cleaned = clean_template_raw_text(raw_text, title)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fixed_template rule_fallback 清洗失败, 回退原文: {}", exc)
        cleaned = raw_text
    if cleaned and len(cleaned) >= _MIN_TEMPLATE_CHARS:
        raw_text = cleaned
    if len(raw_text) < _MIN_TEMPLATE_CHARS:
        return None
    return FixedTemplateEntry(
        template_key=template_key,
        title=canonical_title(template_key, title),
        aliases=aliases_for_key(template_key),
        source_page_start=page_start,
        source_page_end=page_end,
        raw_text=raw_text,
        field_slots=extract_field_slots(raw_text),
        signature_slots=extract_signature_slots(raw_text),
        has_table=has_table_hint(raw_text),
        confidence=_RULE_FALLBACK_CONFIDENCE,
        source="rule_only",
        volume_type=volume_type,
    )


def _dedupe_entries(entries: list[FixedTemplateEntry]) -> list[FixedTemplateEntry]:
    """按 (template_key, volume_type, page_start) 三元组去重, 保留正文最长的一条。"""
    best: dict[tuple[str, Optional[str], Optional[int]], FixedTemplateEntry] = {}
    for entry in entries:
        key = (entry.template_key, entry.volume_type, entry.source_page_start)
        current = best.get(key)
        if current is None or len(entry.raw_text) > len(current.raw_text):
            best[key] = entry
    return sorted(
        best.values(),
        key=lambda item: (
            item.source_page_start or 0,
            item.template_key,
            item.volume_type or "",
        ),
    )


__all__ = ["build_rule_fallback_entries"]
