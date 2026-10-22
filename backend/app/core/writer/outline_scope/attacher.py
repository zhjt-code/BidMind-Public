"""
职责: 把纲要标题挂到指定的 OutlineNodeGenerated 自由论述节点上，并按检索窗口/章名挑选高亲和父节点。
不做: 不抽招标原文、不调用 LLM、不写库、不选择检索窗口。
"""

from __future__ import annotations

import re

from app.core.writer.outline_scope.chapter_retriever import chapter_query_terms
from app.core.writer.outline_scope.contracts import (
    MAX_TITLES_PER_PARENT,
    MAX_TITLES_TOTAL,
    ScopeTitle,
    sanitize_scope_title,
)
from app.schemas.writing import OutlineNodeGenerated

_CATCHALL_KEYWORDS = ("其他资料", "其他证明", "认为必要")
_EXCLUDED_PARENT_KEYWORDS = ("公司简介", "业绩", "人员配置", "类似项目")
_VOLUME_WRAPPER_KEYWORDS = (
    "技术标",
    "商务标",
    "资格标",
    "经济标",
    "价格标",
    "技术卷",
    "商务卷",
)


def iter_enrichable_parents(
    tree: list[OutlineNodeGenerated],
) -> list[OutlineNodeGenerated]:
    """可丰富的自由论述节点：分卷包装除外；兜底章放最后。"""

    specific: list[OutlineNodeGenerated] = []
    catchall: list[OutlineNodeGenerated] = []
    for node in _walk(tree):
        if not _is_enrichable_parent(node):
            continue
        if any(keyword in (node.title or "") for keyword in _CATCHALL_KEYWORDS):
            catchall.append(node)
        else:
            specific.append(node)
    return specific + catchall


def attach_titles_to_parent(
    parent: OutlineNodeGenerated,
    titles: list[ScopeTitle],
    *,
    used_norms: set[str] | None = None,
    remaining_global: int | None = None,
) -> int:
    """把清洗后的标题追加到指定父节点，返回实际挂上的条数。"""

    if parent is None or not titles:
        return 0

    existing = _collect_descendant_norms(parent)
    if used_norms:
        existing |= used_norms
    attached = 0
    budget = MAX_TITLES_TOTAL if remaining_global is None else remaining_global
    for item in titles:
        if attached >= budget:
            break
        cleaned = sanitize_scope_title(item.title)
        if not cleaned:
            continue
        norm = _normalize_title(cleaned)
        if not norm or _is_duplicate(norm, existing):
            continue
        scope_child_count = sum(
            1 for child in parent.children if not child.is_skeleton
        )
        if scope_child_count >= MAX_TITLES_PER_PARENT:
            break
        parent.children.append(
            OutlineNodeGenerated(
                title=cleaned,
                depth=parent.depth + 1,
                sort_order=len(parent.children),
                node_type="free_writing",
                is_required=1,
                score_weight=None,
                children=[],
                is_skeleton=False,
                volume_type=parent.volume_type,
            )
        )
        existing.add(norm)
        if used_norms is not None:
            used_norms.add(norm)
        attached += 1
    return attached


def attach_scope_titles(
    tree: list[OutlineNodeGenerated],
    titles: list[ScopeTitle],
) -> int:
    """无检索时的窄入口：挂到第一个可丰富节点。"""

    parents = iter_enrichable_parents(tree)
    if not parents:
        return 0
    return attach_titles_to_parent(parents[0], titles)


def pick_affinity_parent(
    specific_parents: list[OutlineNodeGenerated],
    title: str,
    *,
    parent_windows: dict[int, str],
) -> OutlineNodeGenerated | None:
    """在已有自由论述章中选高亲和父节点；无人认领返回 None，由调用方落入兜底章。"""

    cleaned = (title or "").strip()
    if not cleaned or not specific_parents:
        return None

    scored: list[tuple[int, OutlineNodeGenerated]] = []
    for parent in specific_parents:
        window = parent_windows.get(id(parent), "")
        window_hit = 2 if cleaned and cleaned in window else 0
        terms = chapter_query_terms(parent.title or "")
        term_hits = sum(1 for term in terms if term and term in cleaned)
        score = window_hit + term_hits
        if score > 0:
            scored.append((score, parent))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _is_volume_wrapper(node: OutlineNodeGenerated) -> bool:
    title = node.title or ""
    return any(keyword in title for keyword in _VOLUME_WRAPPER_KEYWORDS)


def _is_enrichable_parent(node: OutlineNodeGenerated) -> bool:
    if node.node_type != "free_writing":
        return False
    if _is_volume_wrapper(node):
        return False
    title = node.title or ""
    if any(keyword in title for keyword in _EXCLUDED_PARENT_KEYWORDS):
        return False
    return True


def _walk(nodes: list[OutlineNodeGenerated]) -> list[OutlineNodeGenerated]:
    found: list[OutlineNodeGenerated] = []
    for node in nodes:
        found.append(node)
        if node.children:
            found.extend(_walk(node.children))
    return found


def _collect_descendant_norms(node: OutlineNodeGenerated) -> set[str]:
    norms: set[str] = set()
    for child in _walk(node.children or []):
        norm = _normalize_title(child.title or "")
        if norm:
            norms.add(norm)
    self_norm = _normalize_title(node.title or "")
    if self_norm:
        norms.add(self_norm)
    return norms


def _normalize_title(title: str) -> str:
    return re.sub(r"[\s\W]", "", (title or "").lower())


def _is_duplicate(norm: str, existing: set[str]) -> bool:
    if norm in existing:
        return True
    for other in existing:
        if len(norm) > 4 and len(other) > 4 and (norm in other or other in norm):
            return True
        chars_a, chars_b = set(norm), set(other)
        union = len(chars_a | chars_b)
        if union > 0 and len(chars_a & chars_b) / union >= 0.65:
            return True
    return False
