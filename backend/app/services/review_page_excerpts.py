"""审查结果页码摘录装配。

职责: 按声称页码从检索命中块取出原文摘录, 供预览片段高亮
不做: 不改符合度/信封卷角色、不定 bbox、不打开 PDF、不截取 LLM 摘要
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

EXCERPT_LIMIT = 280


def clip_excerpt(text: Any, limit: int = EXCERPT_LIMIT) -> str:
    """空白丢弃; 超长截断。禁止把 None 收成 'None'。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    return raw if len(raw) <= limit else raw[:limit]


def attach_page_excerpts(
    item: dict[str, Any],
    *,
    retrieval_chunks: Optional[Sequence[dict[str, Any]]],
    fallback_document_id: Optional[int],
) -> dict[str, Any]:
    """就地写入 source_page_excerpts, 并在已有 locations 上补 excerpt。

    页集合优先 source_page_locations; 整本无分卷定位时回退
    source_page_numbers + fallback_document_id。
    """
    locations = item.get("source_page_locations") or []
    keys: list[tuple[int, int]] = []
    for loc in locations:
        doc_id = loc.get("document_id")
        page = loc.get("page")
        if isinstance(doc_id, int) and isinstance(page, int):
            keys.append((doc_id, page))
    if not keys:
        fallback = fallback_document_id
        pages = item.get("source_page_numbers") or []
        if isinstance(fallback, int):
            for page in pages:
                if isinstance(page, int):
                    keys.append((fallback, page))

    excerpts: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for doc_id, page in keys:
        if (doc_id, page) in seen:
            continue
        seen.add((doc_id, page))
        excerpt = _excerpt_for_page(retrieval_chunks, doc_id, page)
        if not excerpt:
            continue
        excerpts.append({"document_id": doc_id, "page": page, "excerpt": excerpt})
        for loc in locations:
            if loc.get("document_id") == doc_id and loc.get("page") == page:
                loc["excerpt"] = excerpt
    item["source_page_excerpts"] = excerpts
    for chunk in retrieval_chunks or []:
        if isinstance(chunk, dict):
            chunk.pop("content_preview", None)
            chunk.pop("content", None)
    return item


def _excerpt_for_page(
    retrieval_chunks: Optional[Sequence[dict[str, Any]]],
    document_id: int,
    page: int,
) -> str:
    ranked: list[tuple[int, str]] = []
    for chunk in retrieval_chunks or []:
        if chunk.get("document_id") != document_id or chunk.get("page_num") != page:
            continue
        preview = clip_excerpt(
            chunk.get("content_preview") or chunk.get("content") or chunk.get("excerpt")
        )
        if not preview:
            continue
        role = str(chunk.get("role") or "")
        rank = 0 if role == "primary" else 1
        ranked.append((rank, preview))
    if not ranked:
        return ""
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]
