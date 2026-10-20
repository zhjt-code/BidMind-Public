"""
职责: 把 ParsedDocument + 原始 PDF 构建为质检输入 DocumentModel
      (页事实/块与span/标题树/目录/书签/段落/版式统计)
不做: 不执行检查判定、不落库、不调 LLM、不改 ParsedDocument
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.core.document_qa.contracts import (
    BBox,
    DocumentModel,
    QaBlock,
    QaBookmark,
    QaLine,
    QaPage,
    QaRegion,
    QaSpan,
    QaTocEntry,
)

# 段落收尾标点: 行以其结尾视为段落结束
_PARA_TERMINALS = ("。", "！", "？", "；", "!", "?", ";")

# 调用方未提供版面结果时由本模块自行加载; 显式传 None 表示版面不可用, 不再重复加载
_LOAD_STRUCTURE = object()
# rules/layout.yaml 未配置 page_scope 时的文字层字数上限
_IMAGE_PAGE_MAX_CHARS = 5
# 行方向正弦超过该值视为旋转行(与解析层水印判定同口径)
_ROTATED_SIN = 0.05


def _normalize_bbox(
    bbox: tuple[float, float, float, float],
    width: float,
    height: float,
) -> Optional[BBox]:
    if width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = bbox
    clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
    return (
        clamp(x0 / width), clamp(y0 / height),
        clamp(x1 / width), clamp(y1 / height),
    )


def build_paragraphs(page_text: str) -> list[str]:
    """从降噪后的页文本重建段落: 连续行合并, 遇句读收尾标点切段。"""
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in (page_text or "").split("\n"):
        stripped = line.strip()
        if not stripped:
            if buffer:
                paragraphs.append("".join(buffer))
                buffer = []
            continue
        buffer.append(stripped)
        if stripped.endswith(_PARA_TERMINALS):
            paragraphs.append("".join(buffer))
            buffer = []
    if buffer:
        paragraphs.append("".join(buffer))
    return paragraphs


def _collect_fitz_layer(
    pdf_path: str,
    page_count: int,
    max_span_pages: int,
) -> tuple[list[QaBlock], dict[int, int], dict[int, tuple[str, ...]], list, Optional[str]]:
    """读取 fitz 层: blocks(span样式) + 页旋转 + 批注 + 图片像素; 失败返回降级原因。"""
    from app.core.document_qa.contracts import QaImage

    blocks: list[QaBlock] = []
    rotations: dict[int, int] = {}
    annotations: dict[int, tuple[str, ...]] = {}
    images: list[QaImage] = []

    if page_count > max_span_pages:
        return [], {}, {}, [], f"页数超过 {max_span_pages}, 跳过 span 级取证"

    try:
        import fitz

        with fitz.open(pdf_path) as doc:
            for page_index in range(doc.page_count):
                page = doc[page_index]
                page_num = page_index + 1
                rotations[page_num] = int(page.rotation or 0)
                rect = page.rect
                width, height = float(rect.width), float(rect.height)

                notes: list[str] = []
                try:
                    for annot in page.annots() or ():
                        info = annot.info or {}
                        content = str(info.get("content") or "").strip()
                        notes.append(content or str(annot.type[1] or "annotation"))
                except Exception:  # noqa: BLE001
                    pass
                if notes:
                    annotations[page_num] = tuple(notes[:20])

                blocks.extend(_page_blocks(page.get_text("dict"), page_num, width, height))
                try:
                    for info in page.get_image_info() or []:
                        box = info.get("bbox")
                        bbox = _normalize_bbox(tuple(box), width, height) if box else None
                        if bbox is None:
                            continue
                        images.append(QaImage(
                            page=page_num,
                            pixel_width=int(info.get("width") or 0),
                            pixel_height=int(info.get("height") or 0),
                            bbox=bbox,
                        ))
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] fitz 取证层构建失败(降级为文本检查): {exc}")
        return [], {}, {}, [], f"fitz 取证失败: {str(exc)[:120]}"

    return blocks, rotations, annotations, images, None


_SUPER_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _fold_raised_digits(spans: list[QaSpan]) -> list[QaSpan]:
    """更小、上移、1-2 位数字并进前一个 span。拼不上就保持原样, 调用方不得凭残缺单位报冲突。"""
    from dataclasses import replace

    if len(spans) < 2:
        return list(spans)
    folded = [spans[0]]
    for span in spans[1:]:
        previous = folded[-1]
        token = span.text.strip()
        raised = span.bbox[1] + 0.004 < previous.bbox[1]
        smaller = 0 < span.size <= previous.size * 0.8
        if raised and smaller and token.isdigit() and len(token) <= 2:
            folded[-1] = replace(previous, text=previous.text + token.translate(_SUPER_DIGITS))
            continue
        folded.append(span)
    return folded


def _raw_line(
    raw_line: dict[str, Any],
    page_num: int,
    width: float,
    height: float,
) -> tuple[str, list[QaSpan], Optional[QaLine]]:
    """一行 fitz 文字: (行文本, 有几何的 span, 行对象; 行框无效时为 None)。"""
    parts: list[str] = []
    spans: list[QaSpan] = []
    for raw_span in raw_line.get("spans", []):
        text = raw_span.get("text") or ""
        parts.append(text)
        span_bbox = _normalize_bbox(tuple(raw_span.get("bbox") or (0, 0, 0, 0)), width, height)
        if span_bbox is None:
            continue
        spans.append(QaSpan(
            page=page_num,
            text=text,
            bbox=span_bbox,
            font=str(raw_span.get("font") or ""),
            size=float(raw_span.get("size") or 0.0),
            flags=int(raw_span.get("flags") or 0),
            color=int(raw_span.get("color") or 0),
        ))
    spans = _fold_raised_digits(spans)
    text = "".join(span.text for span in spans) if len(spans) == len(raw_line.get("spans") or []) else "".join(parts)
    line_bbox = _normalize_bbox(tuple(raw_line.get("bbox") or (0, 0, 0, 0)), width, height)
    if not parts or line_bbox is None:
        return text, spans, None
    direction = raw_line.get("dir") or (1.0, 0.0)
    rotated = abs(float(direction[1])) > _ROTATED_SIN
    return text, spans, QaLine(
        page=page_num, text=text, bbox=line_bbox, spans=tuple(spans), rotated=rotated,
    )


def _page_blocks(data: dict[str, Any], page_num: int, width: float, height: float) -> list[QaBlock]:
    """一页 fitz 文本块: 块文字、span 样式与行几何。"""
    blocks: list[QaBlock] = []
    for raw_block in data.get("blocks", []):
        if raw_block.get("type") != 0:
            continue
        spans: list[QaSpan] = []
        lines_text: list[str] = []
        lines: list[QaLine] = []
        for raw_line in raw_block.get("lines", []):
            text, line_spans, line = _raw_line(raw_line, page_num, width, height)
            spans.extend(line_spans)
            if raw_line.get("spans"):
                lines_text.append(text)
            if line is not None:
                lines.append(line)
        block_text = "\n".join(lines_text).strip()
        if not block_text:
            continue
        block_bbox = _normalize_bbox(tuple(raw_block.get("bbox") or (0, 0, 0, 0)), width, height)
        if block_bbox is None:
            continue
        blocks.append(QaBlock(
            page=page_num, text=block_text, bbox=block_bbox,
            spans=tuple(spans), lines=tuple(lines),
        ))
    return blocks


def image_page_max_chars(rules: dict) -> int:
    """扫描件/图片页的文字层字数上限(rules/layout.yaml 的 page_scope)。"""
    scope = (rules.get("layout") or {}).get("page_scope") or {}
    return int(scope.get("max_text_chars", _IMAGE_PAGE_MAX_CHARS))


def is_image_only_page(
    text: str,
    image_count: int,
    has_layout_content: bool,
    max_text_chars: int,
) -> bool:
    """文件自带文字不超过上限, 且页上有图片或版面内容: 扫描件、图片或文字转曲页。"""
    if len("".join((text or "").split())) > max_text_chars:
        return False
    return image_count > 0 or has_layout_content


def _build_visual_paragraphs(blocks: list[QaBlock]) -> dict[int, list[str]]:
    """从 fitz blocks 按页重建段落, 不经 NFKC(字形检查通道)。"""
    by_page: dict[int, list[str]] = {}
    for block in sorted(blocks, key=lambda item: (item.page, item.bbox[1])):
        by_page.setdefault(block.page, []).append(block.text)
    return {page: build_paragraphs("\n".join(texts)) for page, texts in by_page.items()}


def _build_style_profile(blocks: list[QaBlock]) -> dict[str, Any]:
    """版式统计: font|size -> 字符数直方图, 供 layout 离群基线。"""
    histogram: dict[str, int] = {}
    for block in blocks:
        for span in block.spans:
            if not span.text.strip():
                continue
            key = f"{span.font}|{round(span.size, 1)}"
            histogram[key] = histogram.get(key, 0) + len(span.text.strip())
    top = sorted(histogram.items(), key=lambda kv: -kv[1])[:20]
    return {"font_size_chars": dict(top)}


def _attach_toc(
    model: DocumentModel,
    pages_src: list,
    body_texts: dict[int, str],
    pages: list[QaPage],
) -> None:
    """目录条目与印刷页坐标。失败只记日志, 不让目录拖垮整份模型。"""
    toc_zone: set[int] = set()
    try:
        from types import SimpleNamespace

        from app.core.parser.page_coordinates import (
            looks_like_toc_page,
            resolve_toc_page_coordinates,
        )
        from app.core.parser.toc_parser import TocParser

        entries = TocParser().extract_toc_entries(pages_src)
        resolve_pages = pages_src
        if len(entries) < 3:
            from app.core.document_qa.toc_fallback import extract_loose_toc_entries

            fallback_entries, toc_page_nums = extract_loose_toc_entries(body_texts)
            if len(fallback_entries) >= 3:
                entries = fallback_entries
                toc_zone.update(toc_page_nums)
                resolve_pages = [
                    SimpleNamespace(
                        page_num=page.page_num,
                        text="" if page.page_num in toc_page_nums else (
                            body_texts.get(page.page_num, "")
                        ),
                    )
                    for page in pages
                ]
        for page in pages:
            if looks_like_toc_page(body_texts.get(page.page_num, ""), page.page_num, 6):
                toc_zone.add(page.page_num)
        model.toc_zone = toc_zone
        if entries:
            resolution = resolve_toc_page_coordinates(resolve_pages, entries)
            physical_by_key = {
                (resolved.title, resolved.printed_page): resolved.physical_page
                for resolved in resolution.entries
            }
            model.toc_resolution_reliable = bool(resolution.is_reliable)
            model.toc_page_offset = resolution.offset
            model.toc_entries = [
                QaTocEntry(
                    title=entry.title,
                    printed_page=entry.page_num,
                    physical_page=physical_by_key.get((entry.title, entry.page_num)),
                    order_index=index,
                )
                for index, entry in enumerate(entries)
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 目录条目采集失败(忽略): {exc}")
        model.toc_zone = toc_zone


def build_document_model(
    parsed_doc: Any,
    pdf_path: str,
    *,
    document_id: int,
    bid_part: Optional[str],
    max_span_pages: int = 800,
    structure_pages: Any = _LOAD_STRUCTURE,
) -> DocumentModel:
    """构建质检输入模型。

    parsed_doc: PdfParser 产出(页文本已剔除页眉页脚/水印), 无版面结果的页以它为正文权威源;
    pdf_path:   原始 PDF, 用 fitz 补 span 样式/bbox/行几何/旋转/批注(失败仅降级);
    structure_pages: 已加载的版面结果; 缺省时本函数自行加载。有版面的页按区域取 fitz 文字。
    """
    from app.core.document_qa.rules.loader import load_rules
    from app.core.document_qa.text_layer_watermark import strip_text_layer_watermark

    rules = load_rules()
    pages_src = list(getattr(parsed_doc, "pages", None) or [])
    page_count = int(getattr(parsed_doc, "page_count", 0) or 0) or len(pages_src)

    blocks, rotations, annotations, images, degraded_reason = _collect_fitz_layer(
        str(pdf_path), page_count, max_span_pages,
    )
    blocks, watermark_chars = strip_text_layer_watermark(blocks, page_count)
    if watermark_chars:
        logger.info(f"[文档质检] 字体层水印剔除: document={document_id}, chars={watermark_chars}")
    if structure_pages is _LOAD_STRUCTURE:
        structure_pages = _load_structure_pages(str(pdf_path))
    regions: dict[int, list[QaRegion]] = {}
    unknown_labels: dict[str, int] = {}
    content_pages: set[int] = set()
    if structure_pages:
        from app.core.document_qa.region_text import build_page_regions, layout_content_pages
        from app.core.document_qa.structure_headings import exclude_table_blocks, table_regions

        regions, unknown_labels = build_page_regions(blocks, structure_pages, rules)
        content_pages = layout_content_pages(regions)
        blocks = exclude_table_blocks(blocks, table_regions(structure_pages))
    max_text_chars = image_page_max_chars(rules)

    pages: list[QaPage] = []
    body_texts: dict[int, str] = {}
    paragraphs: dict[int, list[str]] = {}
    for src in pages_src:
        page_num = int(getattr(src, "page_num", 0) or 0)
        text = str(getattr(src, "text", "") or "")
        image_count = len(getattr(src, "images", None) or [])
        body_texts[page_num] = text
        paragraphs[page_num] = build_paragraphs(text)
        pages.append(QaPage(
            page_num=page_num,
            width=float(getattr(src, "width", 0.0) or 0.0),
            height=float(getattr(src, "height", 0.0) or 0.0),
            rotation=rotations.get(page_num, 0),
            char_count=len(text),
            image_count=image_count,
            table_count=len(getattr(src, "tables", None) or []),
            annotation_texts=annotations.get(page_num, ()),
            image_only=is_image_only_page(
                text, image_count, page_num in content_pages, max_text_chars,
            ),
        ))

    model = DocumentModel(
        document_id=document_id,
        bid_part=bid_part,
        filename=str(getattr(parsed_doc, "filename", "") or ""),
        page_count=page_count,
        pages=pages,
        blocks=blocks,
        body_texts=body_texts,
        paragraphs=paragraphs,
        visual_paragraphs=_build_visual_paragraphs(blocks),
        style_profile=_build_style_profile(blocks),
        regions=regions,
        unknown_region_labels=unknown_labels,
        images=images,
        degraded_reason=degraded_reason,
    )

    _attach_toc(model, pages_src, body_texts, pages)

    # PDF 书签(fail-open, 无书签返回空)
    try:
        from app.core.parser.bookmark_reader import read_pdf_bookmarks

        model.bookmarks = [
            QaBookmark(level=entry.level, title=entry.title, page=entry.page)
            for entry in read_pdf_bookmarks(pdf_path)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 书签读取失败(忽略): {exc}")

    # 正文标题树: 版面分析成功的页用其标题, 失败页退回启发式检测
    from app.core.document_qa.heading_detector import detect_headings

    model.headings = _resolve_headings(model, structure_pages, rules, detect_headings)
    from app.core.document_qa.author_spans import ensure_author_view

    ensure_author_view(model)
    return model


def _load_structure_pages(pdf_path: str) -> Optional[dict[int, list[dict[str, Any]]]]:
    try:
        from app.core.document_qa.structure_client import fetch_structure_pages

        return fetch_structure_pages(pdf_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 版面分析不可用, 退回标题检测: {exc}")
        return None


def _resolve_headings(
    model: DocumentModel,
    structure_pages: Optional[dict[int, list[dict[str, Any]]]],
    rules: dict,
    detect_headings: Any,
) -> list:
    if not structure_pages:
        return detect_headings(model, rules)
    from app.core.document_qa.structure_headings import headings_from_pages

    structured = headings_from_pages(structure_pages)
    missing = {page.page_num for page in model.pages} - set(structure_pages)
    if not missing:
        return structured
    fallback = [item for item in detect_headings(model, rules) if item.page in missing]
    note = f"版面分析缺 {len(missing)} 页, 这些页退回标题检测"
    model.degraded_reason = f"{model.degraded_reason}; {note}" if model.degraded_reason else note
    return structured + fallback
