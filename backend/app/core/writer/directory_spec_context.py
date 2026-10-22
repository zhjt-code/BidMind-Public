"""写标目录规格提取上下文选择。

职责: 为写标目录规格提取选择可信的招标文件格式页上下文并校验目录规格完整性。
不做: 不调用 LLM、不保存数据库、不生成 writing_outline_nodes。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.parser.toc_parser import deserialize_toc_map
from app.core.writer.format_section_markers import (
    has_line_anchored_format_chapter_heading,
    is_format_section_title,
)
from app.models.chunk import Chunk
from app.models.document import Document
from app.schemas.writing import BidDirectorySpec

# 旧 30 块硬上限已废弃，改为 80 页安全上限
_DEFAULT_MAX_PAGES = 80
_TOP_LEVEL_CHAPTER_RE = re.compile(r"^\s*第[一二三四五六七八九十\d]+[章节]")
_DIRECTORY_SECTION_KEYWORDS = frozenset([
    "投标文件格式", "投标文件目录", "目录格式", "封面及目录",
    "投标文件由", "投标文件内容", "投标文件组成",
])


async def load_document_toc_map(
    db: AsyncSession,
    document_id: int,
) -> dict[str, Any] | None:
    """读取文档 v2 目录页码映射，解析失败时返回 None。"""

    result = await db.execute(
        select(Document.toc_map).where(Document.id == document_id).limit(1)
    )
    toc_json = result.scalar_one_or_none()
    toc_map = deserialize_toc_map(toc_json)
    return toc_map or None


def select_directory_spec_chunks(
    chunks: list[Chunk],
    toc_map: Optional[dict[str, Any]],
    *,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> list[Chunk]:
    """以 v2 toc_map 定位格式章起点，再按章节边界选择正文。"""

    if max_pages <= 0:
        return []
    start_page = _find_format_section_start_page(toc_map)
    if start_page is None:
        return []
    ordered = _order_chunks_by_page(chunks)
    start_index = next(
        (
            index
            for index, chunk in enumerate(ordered)
            if getattr(chunk, "page_num", None) is not None
            and int(chunk.page_num) >= start_page
        ),
        None,
    )
    if start_index is None:
        return []
    return _collect_from_start(
        ordered,
        start_index=start_index,
        max_pages=max_pages,
        stop_on_next_top_level_chapter=True,
    )


def select_directory_spec_chunks_by_keywords(
    chunks: list[Chunk],
    *,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> list[Chunk]:
    """无可靠 toc_map 时按行首格式章标题或关键词选择目录上下文。

    优先从最后一个行首格式章标题（排除目录点线行）开始选取；若无，
    再回退到关键词窗口扫描。两者都不会在未命中时扩展为全文。
    收集边界：下一同级章标题或文档尾，加 80 页安全上限。
    """

    if max_pages <= 0:
        return []

    ordered = _order_chunks_by_page(chunks)
    last_marker_index: int | None = None
    for index, chunk in enumerate(ordered):
        if has_line_anchored_format_chapter_heading(getattr(chunk, "content", None) or ""):
            last_marker_index = index

    if last_marker_index is not None:
        return _collect_from_start(
            ordered,
            start_index=last_marker_index,
            max_pages=max_pages,
            stop_on_next_top_level_chapter=True,
        )

    return _collect_by_directory_keywords(ordered, max_pages=max_pages)


def select_directory_spec_chunks_with_fallback(
    chunks: list[Chunk],
    toc_map: Optional[dict[str, Any]],
    *,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> list[Chunk]:
    """按 toc_map -> 行首格式章/关键词 的优先级选择目录上下文。"""

    selected = select_directory_spec_chunks(chunks, toc_map, max_pages=max_pages)
    if selected:
        return selected
    return select_directory_spec_chunks_by_keywords(chunks, max_pages=max_pages)


def is_usable_directory_spec(spec: Optional[BidDirectorySpec]) -> bool:
    """判断目录规格是否足够进入照搬模式。"""

    if not spec or not spec.has_explicit_directory or not spec.volumes:
        return False
    return all(volume.items for volume in spec.volumes)


def downgrade_empty_directory_spec(spec: BidDirectorySpec) -> BidDirectorySpec:
    """将空显式目录降级为非显式目录，保留模型原始判断说明。"""

    reasoning = (spec.reasoning or "").strip()
    suffix = "目录规格质量门控: 已识别分卷但所有分卷 items 为空，降级为非显式目录以避免生成空骨架。"
    return BidDirectorySpec(
        has_explicit_directory=False,
        volumes=spec.volumes,
        reasoning=f"{reasoning} | {suffix}" if reasoning else suffix,
    )


def _order_chunks_by_page(chunks: list[Chunk]) -> list[Chunk]:
    """按 (page_num 为 None, page_num, chunk_index) 排序，page_num 为 None 的 chunk 置尾。"""

    return sorted(
        chunks,
        key=lambda c: (
            getattr(c, "page_num", None) is None,
            getattr(c, "page_num", None) or 0,
            getattr(c, "chunk_index", 0),
        ),
    )


def _find_format_section_start_page(
    toc_map: Optional[dict[str, Any]],
) -> int | None:
    """返回可靠 toc_map 中最早的格式章物理页。"""

    if not _is_reliable_v2_toc_map(toc_map):
        return None

    pages = [
        int(entry["physical_page"])
        for entry in toc_map.get("entries", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("title"), str)
        and isinstance(entry.get("physical_page"), int)
        and entry["physical_page"] > 0
        and is_format_section_title(str(entry["title"]))
    ]
    return min(pages) if pages else None


def _is_reliable_v2_toc_map(toc_map: Optional[dict[str, Any]]) -> bool:
    return bool(
        isinstance(toc_map, dict)
        and toc_map.get("version") == 2
        and toc_map.get("coordinate_system") == "physical_page"
        and toc_map.get("enabled")
    )


def _collect_from_start(
    ordered: list[Chunk],
    *,
    start_index: int,
    max_pages: int,
    stop_on_next_top_level_chapter: bool,
) -> list[Chunk]:
    """从 start_index 起收集 chunk，直到下一同级章标题或超出页上限。

    使用行首锚定格式章检测判断当前块是否仍属格式章，
    避免目录点线行把窗口提前截断。
    """

    relevant: list[Chunk] = []
    start_chunk = ordered[start_index] if ordered else None
    start_page: int | None = getattr(start_chunk, "page_num", None) if start_chunk else None

    for chunk in ordered[start_index:]:
        text = getattr(chunk, "content", None) or ""
        if (
            stop_on_next_top_level_chapter
            and relevant
            and _is_next_top_level_chapter(text)
            and not has_line_anchored_format_chapter_heading(text)
        ):
            break
        page = getattr(chunk, "page_num", None)
        # ponytail: 80-page ceiling for format-chapter windows; upgrade to TOC-derived chapter end if a chapter exceeds this.
        if page is not None and start_page is not None and page > start_page + max_pages - 1:
            break
        relevant.append(chunk)

    return relevant


def _collect_by_directory_keywords(
    ordered: list[Chunk],
    *,
    max_pages: int,
) -> list[Chunk]:
    """按关键词扫描收集格式章上下文，直到下一同级章标题或超出页上限。"""

    relevant: list[Chunk] = []
    in_section = False
    start_page: int | None = None

    for chunk in ordered:
        text = getattr(chunk, "content", None) or ""
        has_kw = any(keyword in text for keyword in _DIRECTORY_SECTION_KEYWORDS)

        if has_kw and not in_section:
            in_section = True
            start_page = getattr(chunk, "page_num", None)

        if not in_section:
            continue

        if relevant and _is_next_top_level_chapter(text) and not has_kw:
            break

        page = getattr(chunk, "page_num", None)
        # ponytail: 80-page ceiling for format-chapter windows; upgrade to TOC-derived chapter end if a chapter exceeds this.
        if page is not None and start_page is not None and page > start_page + max_pages - 1:
            break

        relevant.append(chunk)

    return relevant


def _is_next_top_level_chapter(text: str) -> bool:
    if not any(token in text for token in ("第", "章", "附件")):
        return False
    return any(
        _TOP_LEVEL_CHAPTER_RE.match(line.strip())
        for line in text.split("\n")[:3]
    )
