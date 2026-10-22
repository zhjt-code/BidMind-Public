"""固定模板 LLM 输入范围定位。

职责: 框定 fixed_template LLM 识别所需的页码范围。
不做: 不识别单个模板、不清洗正文、不访问数据库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.writer.format_section_markers import has_explicit_format_chapter_marker


@dataclass(frozen=True)
class SectionRange:
    """固定模板识别的输入页码范围。"""

    page_start: int
    page_end: int
    volume_type: str | None = None


_FORMAT_HINTS = ("投标文件格式", "响应文件格式", "投标格式")
_TECH_HINTS = ("技术标",)
_COMM_HINTS = ("商务标",)
_TEMPLATE_PAGE_HINTS = ("投 标 文 件", "投标文件", "目 录", "目录", "盖单位章")


def locate_format_chapter_ranges(chunks: list[Any]) -> list[SectionRange]:
    """定位疑似投标文件格式章节；失败时回退全文范围。"""
    pages = _merge_pages(chunks)
    if not pages:
        return []
    ranges = _ranges_from_format_markers(pages)
    if ranges:
        return ranges
    return [SectionRange(page_start=pages[0][0], page_end=pages[-1][0], volume_type=None)]


def render_chunks_for_ranges(chunks: list[Any], ranges: list[SectionRange]) -> str:
    """按页码范围渲染 LLM 输入文本。"""
    if not ranges:
        return ""
    page_map = {page: text for page, text in _merge_pages(chunks)}
    parts: list[str] = []
    for range_item in ranges:
        vol = range_item.volume_type or "unknown"
        for page in range(range_item.page_start, range_item.page_end + 1):
            text = page_map.get(page)
            if text:
                parts.append(f"--- PAGE {page} volume={vol} ---\n{text}")
    return "\n\n".join(parts)


def _merge_pages(chunks: list[Any]) -> list[tuple[int, str]]:
    by_page: dict[int, list[tuple[int, str]]] = {}
    fallback = 100_000
    for chunk in chunks:
        page = getattr(chunk, "page_num", None)
        if page is None:
            page = fallback
            fallback += 1
        content = str(getattr(chunk, "content", "") or "").strip()
        if content:
            by_page.setdefault(int(page), []).append(
                (int(getattr(chunk, "chunk_index", 0) or 0), content),
            )
    merged: list[tuple[int, str]] = []
    for page, items in sorted(by_page.items()):
        text = "\n".join(content for _idx, content in sorted(items, key=lambda item: item[0]))
        merged.append((page, text))
    return merged


def _ranges_from_format_markers(pages: list[tuple[int, str]]) -> list[SectionRange]:
    """根据 markers 推导 fixed_template 候选页范围。: 优先使用硬 marker(严格"第N章/部分/篇 + 投标文件格式"形式);
 硬 marker 命中即视为正文章节起点, 软 marker(子串包含 + 模板封面启发式)
 全部忽略。仅在无硬 marker 时, fallback 到旧的软 marker 路径,
 保持对早期项目数据的向后兼容。

 招标文件常态: 目录页在前、正文章节在后。多个硬 marker 时取**最后一个**
 作为章节起点, 避免被"第五章 投标文件格式..........页码"这种目录条目
 把 markers 范围拉到整个文档(项目 1389 真实失败案例)。
 """
    strong_markers: list[int] = []
    weak_markers: list[int] = []
    for page, text in pages:
        if _has_strong_format_marker(text):
            strong_markers.append(page)
        elif _has_format_marker(text) or _looks_like_template_cover_or_toc(text):
            weak_markers.append(page)
    if strong_markers:
        primary = strong_markers[-1]
        end_basis = max(strong_markers)
    elif weak_markers:
        primary = min(weak_markers)
        end_basis = max(weak_markers)
    else:
        return []
    start = max(1, primary - 1)
    end = min(pages[-1][0], end_basis + 25)
    split = _split_ranges_by_volume_markers(pages, start, end)
    if split:
        return split
    volume = _classify_volume("\n".join(text for page, text in pages if start <= page <= end))
    return [SectionRange(page_start=start, page_end=end, volume_type=volume)]


def _has_strong_format_marker(text: str) -> bool:
    """检查文本是否含"第N章/部分/篇/节 + (投标|响应)文件格式"严格形式。"""
    return has_explicit_format_chapter_marker(text)


def _split_ranges_by_volume_markers(
    pages: list[tuple[int, str]], start: int, end: int,
) -> list[SectionRange]:
    """把已定位的格式章节范围按"技术标/商务标"分卷标记切分。

    职责: 仅在能稳定识别"商务标分卷起点"页时, 切成 technical / commercial 两段。
    不做: 不识别模板、不调用 LLM、不修改 chunks 内容。
    """
    comm_start = _find_commercial_breakpoint(pages, start, end)
    if comm_start is None or comm_start <= start:
        return []
    return [
        SectionRange(page_start=start, page_end=comm_start - 1, volume_type="technical"),
        SectionRange(page_start=comm_start, page_end=end, volume_type="commercial"),
    ]


_COVER_TOKENS_FOR_VOLUME_SPLIT = ("投标文件", "投 标 文 件", "目录", "目 录")


def _find_commercial_breakpoint(
    pages: list[tuple[int, str]], start: int, end: int,
) -> int | None:
    """返回首次出现"商务标"作为分卷起点的页码, 否则 None。: 真实招标文件中商务标章节首页可能交叉引用"技术标"
 (如"按技术标章节执行"), 仅靠"商务标 ∧ ¬技术标"二字孤词易漏判。
 本函数按"信号 1 > 信号 2 > 信号 3"优先级投票:
 - 信号 1: 含"商务标"且不含"技术标" (最严格, 旧逻辑)
 - 信号 2: 含"商务标"+模板封面 token (投标文件/目录) 即使同页有"技术标"
 - 信号 3: 含"商务标"且与上一商务标候选页连续 >=2 页 (后备, 此处暂不实现)
 """
    signal2_hit: int | None = None
    for page, text in pages:
        if page < start or page > end:
            continue
        compact = re.sub(r"\s+", "", text or "")
        has_comm = any(hint in compact for hint in _COMM_HINTS)
        if not has_comm:
            continue
        has_tech = any(hint in compact for hint in _TECH_HINTS)
        if not has_tech:
            return page
        if signal2_hit is None and any(
            token.replace(" ", "") in compact for token in _COVER_TOKENS_FOR_VOLUME_SPLIT
        ):
            signal2_hit = page
    return signal2_hit


def _has_format_marker(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return any(hint in compact for hint in _FORMAT_HINTS)


def _looks_like_template_cover_or_toc(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not any(hint.replace(" ", "") in compact for hint in _TEMPLATE_PAGE_HINTS):
        return False
    subitem_count = len(re.findall(r"[一二三四五六七八九十]+[、.．]", compact))
    has_volume = any(hint in compact for hint in _TECH_HINTS + _COMM_HINTS)
    return has_volume or subitem_count >= 3


def _classify_volume(text: str) -> str | None:
    if any(hint in text for hint in _COMM_HINTS) and not any(hint in text for hint in _TECH_HINTS):
        return "commercial"
    if any(hint in text for hint in _TECH_HINTS) and not any(hint in text for hint in _COMM_HINTS):
        return "technical"
    return None
