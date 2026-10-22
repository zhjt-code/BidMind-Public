"""固定模板章节保真渲染（verbatim 通道）。

职责: 将 fixed_template 章节草稿按原文行流写入 Word 段落与预览 HTML,
 不剥离、不重排模板原有编号。
不做: 不推断标题层级、不做统一编号契约、不净化占位符(由调用方预处理)、
 不访问数据库。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bs4 import BeautifulSoup, NavigableString, Tag
from loguru import logger

from app.core.xml_compat import sanitize_xml_compatible_text

if TYPE_CHECKING:
    from app.core.writer.doc_renderer import DocRenderer, HtmlPreviewBuilder
    from app.models.writing_outline_node import WritingOutlineNode


def is_fixed_template_node(node: "WritingOutlineNode") -> bool:
    """判断节点是否走固定模板保真渲染。"""
    node_type = getattr(node, "node_type", None)
    value = getattr(node_type, "value", node_type)
    return str(value or "") == "fixed_template"


def iter_verbatim_blocks(html: str) -> list[tuple[str, str]]:
    """把章节内容拆成保真块序列。

    返回 (kind, payload) 列表:
    - ("text", 行文本): 原样输出为正文段落, 保留行首编号字符
    - ("html", 片段): 表格等结构块, 原样透传
    兼容两种草稿形态: 生成链路的"纯文本行 + <table>", 与用户在编辑器
    保存后的 <p> 包裹 HTML。
    """
    blocks: list[tuple[str, str]] = []
    soup = BeautifulSoup(html or "", "html.parser")
    root = soup.body or soup
    for node in root.contents:
        blocks.extend(_blocks_from_node(node))
    return blocks


def write_verbatim_docx_section(
    renderer: "DocRenderer",
    doc: object,
    html: str,
    Pt: type,
    image_records: dict | None,
    section_title: str,
    consumed_image_keys: dict[str, str] | None,
) -> None:
    """fixed_template 草稿 -> Word: 逐行原样段落 + 表格保真直写 + 图片占位插入。"""
    raw_html = (html or "").strip()
    processed = renderer._preprocess_html_for_bid(raw_html, section_title)
    start_para_index = len(doc.paragraphs)
    for kind, payload in iter_verbatim_blocks(processed):
        if kind == "html":
            # 带 rowspan/colspan 的保真表格必须绕过 htmldocx
            # (其列数补齐归一化会摊平跨度), 用 python-docx 网格 + merge 直写。
            if not _write_spanned_table_to_docx(doc, payload):
                renderer._append_html_fragment_to_doc(payload, doc)
        else:
            renderer._render_body_paragraph(doc, payload)
    renderer._insert_figure_images(
        doc,
        raw_html,
        Pt,
        image_records or {},
        start_para_index,
        section_title,
        consumed_image_keys,
    )


def render_verbatim_preview_html(
    builder: "HtmlPreviewBuilder",
    processed_html: str,
) -> str:
    """fixed_template 草稿 -> 预览 HTML: 逐行 <p> + 表格原样。

    文本行经 builder._decorate_text_block 输出, 与 Word 路径共享
    占位符字符层替换语义; 图片占位段落保持 <p>[图片占位]</p> 形态,
    由调用方的 inline_section_images 统一消费。
    """
    parts: list[str] = []
    for kind, payload in iter_verbatim_blocks(processed_html):
        if kind == "html":
            parts.append(payload)
        else:
            parts.append(builder._decorate_text_block(payload))
    return "\n".join(parts)


def _write_spanned_table_to_docx(doc: object, table_html: str) -> bool:
    """把带 rowspan/colspan 的 <table> 用 python-docx 网格 + merge 保真写入。

    返回 False 表示无法解析(交由调用方回退 htmldocx 通道)。
    """
    soup = BeautifulSoup(table_html or "", "html.parser")
    table_tag = soup.find("table")
    if table_tag is None:
        return False
    rows = table_tag.find_all("tr")
    if not rows:
        return False

    # 第一遍: 依跨度推进占位, 推导网格尺寸与每个单元格的落点
    occupied: set[tuple[int, int]] = set()
    placements: list[tuple[int, int, int, int, str]] = []
    n_cols = 0
    for row_index, tr in enumerate(rows):
        col_cursor = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while (row_index, col_cursor) in occupied:
                col_cursor += 1
            try:
                rowspan = max(1, int(cell.get("rowspan") or 1))
                colspan = max(1, int(cell.get("colspan") or 1))
            except (TypeError, ValueError):
                rowspan, colspan = 1, 1
            text = " ".join(cell.get_text(" ", strip=True).split())
            placements.append((row_index, col_cursor, rowspan, colspan, text))
            for r in range(row_index, row_index + rowspan):
                for c in range(col_cursor, col_cursor + colspan):
                    occupied.add((r, c))
            col_cursor += colspan
            n_cols = max(n_cols, col_cursor)
    n_rows = max((r + rs for r, _c, rs, _cs, _t in placements), default=len(rows))
    if n_cols < 1 or n_rows < 1:
        return False

    try:
        docx_table = doc.add_table(rows=n_rows, cols=n_cols)
        docx_table.style = "Table Grid"
        for row_index, col_index, rowspan, colspan, text in placements:
            target = docx_table.cell(row_index, col_index)
            if rowspan > 1 or colspan > 1:
                target = target.merge(
                    docx_table.cell(row_index + rowspan - 1, col_index + colspan - 1)
                )
            target.text = sanitize_xml_compatible_text(text)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("verbatim 表格保真写入失败, 回退 htmldocx: {}", exc)
        return False


def _blocks_from_node(node: object) -> list[tuple[str, str]]:
    if isinstance(node, NavigableString):
        return [
            ("text", line.strip())
            for line in str(node).splitlines()
            if line.strip()
        ]
    if not isinstance(node, Tag):
        return []
    name = (node.name or "").lower()
    if name == "br":
        return []
    if name == "table":
        return [("html", str(node))]
    if name in {"ul", "ol"}:
        return [
            ("text", text)
            for li in node.find_all("li", recursive=False)
            if (text := _tag_text(li))
        ]
    if name in {"section", "article"}:
        blocks: list[tuple[str, str]] = []
        for child in node.contents:
            blocks.extend(_blocks_from_node(child))
        return blocks
    if node.find("table") is not None:
        return [("html", str(node))]
    text = _tag_text(node)
    return [("text", text)] if text else []


def _tag_text(tag: Tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


__all__ = [
    "is_fixed_template_node",
    "iter_verbatim_blocks",
    "render_verbatim_preview_html",
    "write_verbatim_docx_section",
]
