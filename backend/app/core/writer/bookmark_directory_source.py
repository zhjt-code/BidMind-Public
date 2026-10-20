"""
职责: 从PDF书签条目定位投标文件格式章子树并零LLM构建目录规格
不做: 不读PDF文件、不调用LLM、不做文本选段、不落库
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from app.core.writer.directory_spec_alignment import build_numbered_line_inventory
from app.core.writer.format_section_markers import (
    has_line_anchored_format_chapter_heading,
    is_format_chapter_toc_page_title,
)
from app.core.writer.locator_volume import strip_locator_wrappers_from_spec
from app.schemas.writing import BidDirectoryItem, BidDirectorySpec, BidVolumeSpec

# 用于单卷场景：去除章标题中的"第N章/部分/篇/节"前缀，保留正文语义
_CHAPTER_PREFIX_RE = re.compile(
    r"^第\s*[一二三四五六七八九十百零\d]+\s*(?:章|部分|篇|节)\s*"
)

_MIN_SUBTREE_COUNT = 5

# 内部类型别名：(层级, 标题, 页码)，层级从 1 开始
_Entry = tuple[int, str, int]


def _strip_chapter_prefix(title: str) -> str:
    """去除书签标题开头的"第N章/部分/篇/节"，返回正文部分。"""
    return _CHAPTER_PREFIX_RE.sub("", title).strip()


def _split_numbered_title(title: str) -> tuple[Optional[str], str]:
    """拆分行首编号与正文标题。

    利用 build_numbered_line_inventory 解析编号前缀。
    拆出 → (display_no, clean_title)；无编号 → (None, stripped_title)。
    """
    nls = build_numbered_line_inventory(title)
    if nls:
        return nls[0].display_no, nls[0].title
    return None, title.strip()


def _map_volume_type(vol_title: str) -> str:
    """按卷标题关键词映射 volume_type，按优先级命中即止。"""
    if "资格" in vol_title:
        return "qualification"
    if "报价" in vol_title or "价格" in vol_title:
        return "pricing"
    if "商务" in vol_title and "技术" in vol_title:
        return "commercial"
    if "技术" in vol_title:
        return "technical"
    if "商务" in vol_title:
        return "commercial"
    return "other"


def _vol_has_children(subtree: list[_Entry], vol_idx: int, vol_level: int) -> bool:
    """检查 subtree[vol_idx] 处的卷候选是否有至少一个 level > vol_level 的子条目。

    扫描到 level <= vol_level 时（下一卷候选或更浅）停止并返回 False。
    """
    for lv, _, _ in subtree[vol_idx + 1:]:
        if lv <= vol_level:
            return False
        return True  # 第一个 lv > vol_level 即确认有子条目
    return False


def _build_children(
    entries: list[_Entry],
    start: int,
    parent_level: int,
    source_span: str,
) -> tuple[list[BidDirectoryItem], int]:
    """递归从 start 位置收集 level > parent_level 的条目为子节点列表。

    遇到 level <= parent_level 的条目停止。
    层级跳跃（如 L1 直接到 L3）时，将跳跃条目挂至当前父节点，不崩。
    返回 (items, next_start_idx)。
    """
    items: list[BidDirectoryItem] = []
    order = 1
    i = start
    n = len(entries)
    while i < n:
        lv, title, _ = entries[i]
        if lv <= parent_level:
            break
        children, i = _build_children(entries, i + 1, lv, source_span)
        display_no, clean_title = _split_numbered_title(title)
        items.append(
            BidDirectoryItem(
                order=order,
                title=clean_title or title.strip(),
                display_no=display_no,
                node_type="free_writing",
                node_type_source="fallback",
                source_span=source_span,
                source_verified=True,
                children=children,
            )
        )
        order += 1
    return items, i


def _divide_into_volumes(
    subtree: list[_Entry],
    chapter_level: int,
    chapter_title: str,
    source_span: str,
) -> list[BidVolumeSpec]:
    """将格式章子树划分为卷列表。

    多卷条件：直接子层 (chapter_level+1) 存在 >= 2 个候选且每个都有子条目。
    否则整个子树作为单卷，卷名取格式章标题去"第N章"前缀后的正文。
    """
    vol_level = chapter_level + 1
    vol_indices = [i for i, (lv, _, _) in enumerate(subtree) if lv == vol_level]

    is_multi = len(vol_indices) >= 2 and all(
        _vol_has_children(subtree, vi, vol_level) for vi in vol_indices
    )

    if is_multi:
        volumes: list[BidVolumeSpec] = []
        for k, vi in enumerate(vol_indices):
            _, raw_title, _ = subtree[vi]
            _, vol_title = _split_numbered_title(raw_title)
            next_vi = vol_indices[k + 1] if k + 1 < len(vol_indices) else len(subtree)
            vol_entries = subtree[vi + 1:next_vi]
            items, _ = _build_children(vol_entries, 0, vol_level, source_span)
            volumes.append(
                BidVolumeSpec(
                    volume_type=_map_volume_type(vol_title),
                    volume_title=vol_title,
                    items=items,
                )
            )
        return volumes

    # 单卷：整个子树作为一卷，卷名来自格式章标题
    vol_title = _strip_chapter_prefix(chapter_title) or chapter_title
    items, _ = _build_children(subtree, 0, chapter_level, source_span)
    return [
        BidVolumeSpec(
            volume_type=_map_volume_type(vol_title),
            volume_title=vol_title,
            items=items,
        )
    ]


def build_spec_from_bookmarks(
    entries: Sequence[tuple[int, str, int]],
) -> Optional[BidDirectorySpec]:
    """从PDF书签条目零LLM构建投标文件目录规格。

    算法摘要：
    1. 扫描 entries，用 has_line_anchored_format_chapter_heading 定位格式章，
       多命中取最后一个（正文章节常在目录页书签之后）。
    2. 截取格式章之后 level > chapter_level 的连续子树，遇同级或更浅即止。
    3. 质量门槛：子树条目 >= 5 且存在相对深度 >= 2 的条目（非仅卷层）。
    4. 卷划分：直接子层候选 >= 2 且各有子条目 → 多卷；否则单卷。
    5. 递归构建 BidDirectoryItem，用 build_numbered_line_inventory 拆编号前缀。

    参数:
        entries: 书签条目序列，每项为 (level, title, page)，level 从 1 开始，
                 title 应为非空字符串

    返回:
        成功时返回 BidDirectorySpec(has_explicit_directory=True)；
        未命中格式章或不过质量门槛时返回 None，由上层回退 LLM 链路
    """
    if not entries:
        return None

    flat = list(entries)

    # 定位格式章：多命中取最后一个
    chapter_idx: Optional[int] = None
    for i, (_, title, _) in enumerate(flat):
        if has_line_anchored_format_chapter_heading(title):
            chapter_idx = i

    if chapter_idx is None:
        return None

    chapter_level, chapter_title, _ = flat[chapter_idx]

    # 截取子树；格式章印刷「目录」页不是投标章节
    subtree: list[_Entry] = []
    for lv, title, page in flat[chapter_idx + 1:]:
        if lv <= chapter_level:
            break
        if is_format_chapter_toc_page_title(title):
            continue
        subtree.append((lv, title, page))

    # 质量门槛一：条目数
    if len(subtree) < _MIN_SUBTREE_COUNT:
        return None
    # 质量门槛二：相对深度（除卷层外还有条目层）
    if not any(lv >= chapter_level + 2 for lv, _, _ in subtree):
        return None

    source_span = f"PDF书签 {chapter_title}"
    volumes = _divide_into_volumes(subtree, chapter_level, chapter_title, source_span)

    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=volumes,
        reasoning=f"来源于PDF书签: {chapter_title}, 共{len(subtree)}条",
    )
    return strip_locator_wrappers_from_spec(spec)
