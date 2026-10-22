"""
职责: 省略招标格式章 locator 卷包装，过滤印刷目录页，并对兜底节点去重
不做: 不调用 LLM、不落库、不推断 node_type、不生成 OutlineNode 骨架
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.writer.format_section_markers import (
    is_format_chapter_locator_title,
    is_format_chapter_toc_page_title,
)
from app.schemas.writing import BidDirectoryItem, BidDirectorySpec, OutlineNodeGenerated


def should_omit_volume_wrapper(volume_title: str) -> bool:
    """格式章定位标题不得落成投标目录根节点。"""

    return is_format_chapter_locator_title(volume_title)


def _is_locator_wrapper_title(title: str) -> bool:
    """章定位名与印刷目录页都不是投标目录条目。"""

    return is_format_chapter_locator_title(title) or is_format_chapter_toc_page_title(
        title
    )


def filter_locator_toc_items(
    items: Sequence[BidDirectoryItem],
) -> list[BidDirectoryItem]:
    """去掉格式章定位包装与印刷「目录」页，将其下真实条目提升到当前层。"""

    result: list[BidDirectoryItem] = []
    for item in items:
        children = filter_locator_toc_items(item.children)
        if _is_locator_wrapper_title(item.title):
            result.extend(children)
            continue
        result.append(item.model_copy(update={"children": children}))
    return result


def strip_locator_wrappers_from_spec(spec: BidDirectorySpec) -> BidDirectorySpec:
    """从规格 items 中剥离定位包装；保留 volume_title 供照搬层省略卷根。"""

    volumes = [
        vol.model_copy(update={"items": filter_locator_toc_items(vol.items)})
        for vol in spec.volumes
    ]
    return spec.model_copy(update={"volumes": volumes})


def dedup_catchall_among_siblings(
    siblings: Sequence[OutlineNodeGenerated],
    catchall_keywords: Sequence[str],
) -> int:
    """移除兜底节点中与同级非兜底标题重复的非骨架子节点。

    骨架子节点始终保留。返回移除条数。
    """

    catchall_children: list[OutlineNodeGenerated] = []
    non_catchall_children: list[OutlineNodeGenerated] = []
    for child in siblings:
        if any(kw in child.title for kw in catchall_keywords):
            catchall_children.append(child)
        else:
            non_catchall_children.append(child)

    if not catchall_children:
        return 0

    non_catchall_sub_titles: set[str] = set()
    for nc in non_catchall_children:
        non_catchall_sub_titles.add(nc.title.strip())
        for sub in nc.children or []:
            non_catchall_sub_titles.add(sub.title.strip())

    removed = 0
    for ca in catchall_children:
        if not ca.children:
            continue
        original_count = len(ca.children)
        ca.children = [
            sub for sub in ca.children
            if sub.is_skeleton or sub.title.strip() not in non_catchall_sub_titles
        ]
        removed += original_count - len(ca.children)
    return removed
