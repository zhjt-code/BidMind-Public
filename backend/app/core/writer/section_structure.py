"""章节正文结构规范化与块解析工具。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag

from app.core.writer.numbering import (
    detect_visible_numbering_depth,
    strip_existing_number,
    strip_existing_number_strict,
)

_SPACE_RE = re.compile(r"\s+")
_RESIDUAL_NUMBER_PREFIX_RE = re.compile(r"^\d+[\t \u3000]+(?=\S)")
_PURE_NUMBER_RE = re.compile(r"^\d+$")
_PUNCT_ONLY_RE = re.compile(r"^[¥￥、.．:：;；,，()]+$")
_NUMBER_STUB_RE = re.compile(r"^\d+\s*[.．、]?\s*$")
_MARKDOWN_HTML_FENCE_RE = re.compile(
    r"^\s*```(?:\s*html)?[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)?```\s*$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class SectionListItem:
    text: str
    children: list["SectionBlock"] = field(default_factory=list)


@dataclass
class SectionBlock:
    kind: str
    text: str = ""
    level: int = 0
    ordered: bool = False
    items: list[SectionListItem] = field(default_factory=list)
    html: str = ""
    explicit_depth: Optional[int] = None
    pure_numbering: bool = False
    prefer_structure_depth: bool = False


def normalize_section_html(html: str, section_title: str = "") -> str:
    """清洗章节 HTML，移除重复标题并规范内部标题文本。"""
    raw_html = (html or "").strip()
    if not raw_html:
        return ""
    raw_html = strip_markdown_html_fence(raw_html)

    target = _normalize_text(strip_existing_number(section_title)) if section_title else ""
    if target:
        raw_html = _drop_duplicate_plain_text_title_lines(raw_html, target)

    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in list(soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])):
        text = _normalize_text(tag.get_text(" ", strip=True))
        clean = strip_existing_number(text)
        if target and _normalize_text(clean) == target:
            tag.decompose()
            continue
        tag.name = "h4" if tag.name in {"h1", "h2", "h3", "h4"} else "h5"
        tag.clear()
        tag.append(clean)

    for tag in list(soup.find_all(["p", "div"])):
        if tag.find(["table", "ul", "ol", "figure"]):
            continue
        text = _normalize_text(tag.get_text(" ", strip=True))
        if not text:
            tag.decompose()
            continue
        if target and _normalize_text(strip_existing_number(text)) == target:
            tag.decompose()
            continue

    if target:
        for text_node in list(soup.find_all(string=True)):
            parent = getattr(text_node, "parent", None)
            if parent is None or getattr(parent, "name", None) in {"script", "style"}:
                continue
            normalized = _normalize_text(strip_existing_number(str(text_node)))
            if normalized == target:
                text_node.extract()

    return str(soup)


def parse_section_blocks(html: str) -> list[SectionBlock]:
    """将章节 HTML 解析为稳定块结构。"""
    if not html.strip():
        return []

    soup = BeautifulSoup(html, "html.parser")
    root: Tag = soup.body or soup  # type: ignore[assignment]
    blocks: list[SectionBlock] = []
    for child in root.contents:
        blocks.extend(_parse_node(child))
    return blocks


def detect_numbering_depth(text: str) -> Optional[int]:
    """根据文本前缀判断对应的全局编号层级。"""
    return detect_visible_numbering_depth(text)


def extract_text_without_number(text: str) -> str:
    """移除文本前缀编号并规整空白。"""
    cleaned = _normalize_text(strip_existing_number_strict(text))
    cleaned = _RESIDUAL_NUMBER_PREFIX_RE.sub("", cleaned)
    cleaned = _normalize_text(cleaned)
    if _PURE_NUMBER_RE.fullmatch(cleaned) or _PUNCT_ONLY_RE.fullmatch(cleaned):
        return ""
    return cleaned


def _parse_node(node: object) -> list[SectionBlock]:
    if isinstance(node, NavigableString):
        return _parse_text_node(str(node))
    if not isinstance(node, Tag):
        return []

    name = node.name.lower()
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text:
            return []
        explicit_depth = detect_numbering_depth(text)
        cleaned = extract_text_without_number(text)
        if explicit_depth is not None and not cleaned:
            return [SectionBlock(
                kind="heading",
                text="",
                level=_relative_heading_level(name),
                explicit_depth=explicit_depth,
                pure_numbering=True,
            )]
        if not cleaned:
            return []
        return [SectionBlock(
            kind="heading",
            text=cleaned,
            level=_relative_heading_level(name),
            explicit_depth=explicit_depth,
            prefer_structure_depth=explicit_depth is not None,
        )]

    if name == "table":
        return [SectionBlock(kind="table", html=str(node))]

    if name in {"ul", "ol"}:
        items = [_parse_list_item(li) for li in node.find_all("li", recursive=False)]
        items = [item for item in items if item.text or item.children]
        if not items:
            return []
        return [SectionBlock(kind="list", ordered=name == "ol", items=items)]

    if name in {"p", "div"}:
        if node.find("table"):
            return [SectionBlock(kind="html", html=str(node))]
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text:
            return []
        if re.match(r"^[)）]\s+\S+", text):
            text = re.sub(r"^[)）]\s*", "", text).strip()
            if not text:
                return []
        if _PUNCT_ONLY_RE.fullmatch(text):
            return []
        explicit_depth = detect_numbering_depth(text)
        if explicit_depth is not None:
            cleaned = extract_text_without_number(text)
            if not cleaned:
                return [SectionBlock(
                    kind="numbered_paragraph",
                    text="",
                    explicit_depth=explicit_depth,
                    pure_numbering=True,
                )]
            return [SectionBlock(
                kind="numbered_paragraph",
                text=cleaned,
                explicit_depth=explicit_depth,
                prefer_structure_depth=True,
            )]
        if _looks_like_residual_numbered_text(text):
            cleaned = extract_text_without_number(text)
            if not cleaned:
                return []
            return [SectionBlock(kind="numbered_paragraph", text=cleaned)]
        return [SectionBlock(kind="paragraph", text=text)]

    if name == "br":
        return []

    if name in {"section", "article"}:
        blocks: list[SectionBlock] = []
        for child in node.contents:
            blocks.extend(_parse_node(child))
        return blocks

    text = _normalize_text(node.get_text(" ", strip=True))
    if text:
        return [SectionBlock(kind="paragraph", text=text)]
    return []


def _parse_list_item(li: Tag) -> SectionListItem:
    parts: list[str] = []
    children: list[SectionBlock] = []
    for child in li.contents:
        if isinstance(child, NavigableString):
            text = _normalize_text(str(child))
            if text:
                parts.append(text)
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in {"ul", "ol"}:
            children.extend(_parse_node(child))
            continue
        text = _normalize_text(child.get_text(" ", strip=True))
        if text:
            parts.append(text)

    text = extract_text_without_number(" ".join(parts))
    if not text:
        return SectionListItem(text="", children=children)
    return SectionListItem(text=text, children=children)


def _parse_text_node(text: str) -> list[SectionBlock]:
    blocks: list[SectionBlock] = []
    for clean in _prepare_text_lines(text):
        if not clean:
            continue
        if re.match(r"^[)）]\s+\S+", clean):
            clean = re.sub(r"^[)）]\s*", "", clean).strip()
            if not clean:
                continue
        if _PUNCT_ONLY_RE.fullmatch(clean):
            continue
        explicit_depth = detect_numbering_depth(clean)
        if explicit_depth is not None:
            normalized = extract_text_without_number(clean)
            if not normalized:
                blocks.append(SectionBlock(
                    kind="numbered_paragraph",
                    text="",
                    explicit_depth=explicit_depth,
                    pure_numbering=True,
                ))
                continue
            blocks.append(SectionBlock(
                kind="numbered_paragraph",
                text=normalized,
                explicit_depth=explicit_depth,
                prefer_structure_depth=True,
            ))
        elif _looks_like_residual_numbered_text(clean):
            normalized = extract_text_without_number(clean)
            if not normalized:
                continue
            blocks.append(SectionBlock(kind="numbered_paragraph", text=normalized))
        else:
            blocks.append(SectionBlock(kind="paragraph", text=clean))
    return blocks


def _relative_heading_level(tag_name: str) -> int:
    if tag_name in {"h1", "h2", "h3", "h4"}:
        return 1
    if tag_name == "h5":
        return 2
    return 3


def _normalize_text(text: str) -> str:
    return _SPACE_RE.sub(" ", text or "").strip()


def strip_markdown_html_fence(raw_html: str) -> str:
    """剥离 LLM 常见的整段 HTML Markdown 围栏。"""
    match = _MARKDOWN_HTML_FENCE_RE.match(raw_html)
    if not match:
        return raw_html
    body = match.group("body").strip()
    return body if "<" in body and ">" in body else raw_html


def _drop_duplicate_plain_text_title_lines(raw_html: str, target: str) -> str:
    """清理纯文本模板中与章节标题重复的整行。

    fixed_template 的原始内容常是“纯文本 + 换行”，BeautifulSoup 会将整段
    作为一个文本节点，导致后续仅靠节点级相等判断无法删掉 `（一）投标函`
    这类内部重复标题。这里仅对不含 HTML 标签的纯文本做保守逐行清理。
    """
    if "<" in raw_html or "\n" not in raw_html:
        return raw_html

    kept_lines: list[str] = []
    for line in raw_html.splitlines():
        if _normalize_text(strip_existing_number(line)) == target:
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)


def _prepare_text_lines(text: str) -> list[str]:
    raw_lines = [_normalize_text(line) for line in text.splitlines()]
    raw_lines = [line for line in raw_lines if line]
    prepared: list[str] = []
    idx = 0
    while idx < len(raw_lines):
        line = raw_lines[idx]
        next_line = raw_lines[idx + 1] if idx + 1 < len(raw_lines) else ""
        if _NUMBER_STUB_RE.fullmatch(line) and next_line and not _PUNCT_ONLY_RE.fullmatch(next_line):
            prepared.append(f"{re.sub(r'\\s+', '', line)} {next_line}")
            idx += 2
            continue
        prepared.append(line)
        idx += 1
    return prepared


def _looks_like_residual_numbered_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return bool(_RESIDUAL_NUMBER_PREFIX_RE.match(normalized) or _PURE_NUMBER_RE.fullmatch(normalized))
