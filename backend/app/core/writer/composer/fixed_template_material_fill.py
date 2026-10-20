"""固定模板文字素材补充。

职责: 判断 fixed_template 章节是否允许并如何插入已绑定文字素材事实。
不做: 不匹配素材、不查询数据库、不调用 LLM、不改写固定模板原始结构。
"""

from __future__ import annotations

import re
from html import escape
from typing import Any

_TEXT_MATERIAL_TYPES = {"text", "old_bid_section", "template_fragment"}
_SUPPLEMENT_TITLE_KEYWORDS = ("公司简介", "企业简介", "其他资料", "资料索引", "评分资料")
_SUPPLEMENT_TEMPLATE_MARKERS = ("格式自定", "自行编制", "投标人认为需要补充")
_MAX_SUPPLEMENT_TEXT_CHARS = 4000


def append_fixed_template_material_facts(
    content: str,
    materials: list[Any],
    *,
    node_title: str,
    template_text: str,
) -> tuple[str, list[int], dict[int, str]]:
    """为允许补充的 fixed_template 章节追加文字素材事实。"""
    text_materials = [m for m in materials if _is_text_material(m)]
    if not text_materials or not _allows_text_supplement(node_title, template_text):
        return content, [], {}

    consumed_ids: list[int] = []
    evidence: dict[int, str] = {}
    parts = [
        "<section data-material-supplement=\"true\">",
        "<h4>素材补充说明</h4>",
    ]
    for material in text_materials:
        snippet = _extract_fact_snippet(material)
        material_id = getattr(material, "id", None)
        if not snippet or material_id is None:
            continue
        parts.append(f"<p>{escape(snippet)}</p>")
        consumed_ids.append(int(material_id))
        evidence[int(material_id)] = snippet
    parts.append("</section>")

    if not consumed_ids:
        return content, [], {}
    return f"{content}\n" + "\n".join(parts), consumed_ids, evidence


def _is_text_material(material: Any) -> bool:
    material_type = getattr(material, "material_type", "")
    value = material_type.value if hasattr(material_type, "value") else str(material_type)
    return value in _TEXT_MATERIAL_TYPES


def _allows_text_supplement(node_title: str, template_text: str) -> bool:
    haystack = f"{node_title or ''} {template_text or ''}"
    return any(keyword in haystack for keyword in _SUPPLEMENT_TITLE_KEYWORDS) or any(
        marker in haystack for marker in _SUPPLEMENT_TEMPLATE_MARKERS
    )


def _extract_fact_snippet(material: Any) -> str:
    content = getattr(material, "content", "") or ""
    compact = re.sub(r"\s+", " ", (content or "").strip())
    if not compact:
        return ""
    compact = _strip_leading_material_title(compact, material)
    if len(compact) <= _MAX_SUPPLEMENT_TEXT_CHARS:
        return compact
    return _truncate_at_sentence_boundary(compact, _MAX_SUPPLEMENT_TEXT_CHARS)


def _truncate_at_sentence_boundary(text: str, max_chars: int) -> str:
    clipped = text[:max_chars].rstrip()
    boundary = max(
        clipped.rfind("。"),
        clipped.rfind("；"),
        clipped.rfind(";"),
        clipped.rfind("\n"),
    )
    if boundary >= max_chars // 2:
        return clipped[: boundary + 1].strip()
    return clipped.strip()


def _strip_leading_material_title(content: str, material: Any) -> str:
    for title in _material_title_candidates(material):
        if not title:
            continue
        pattern = rf"^{re.escape(title)}(?:\s+|[:：,，。；;、-]+)"
        content = re.sub(pattern, "", content).strip()
    return content


def _material_title_candidates(material: Any) -> list[str]:
    candidates = [
        getattr(material, "display_title", None),
        getattr(material, "title", None),
        getattr(material, "file_name_label", None),
        getattr(material, "resolved_label", None),
        getattr(material, "structure_hint_label", None),
    ]
    result: list[str] = []
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        result.append(text)
        result.append(re.sub(r"\.[A-Za-z0-9]{1,8}$", "", text).strip())
    deduped: list[str] = []
    for item in result:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


__all__ = ["append_fixed_template_material_facts"]
