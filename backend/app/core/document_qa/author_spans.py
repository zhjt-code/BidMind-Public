"""
职责: 把版面区域和文本行收成表外标题、正文跨度、网格行
不做: 不判定缺陷、不调用版面服务、不解析单元格 HTML
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.document_qa.contracts import BBox, DocumentModel, QaBlock, QaHeading, QaLine
from app.core.document_qa.region_text import (
    KIND_BODY,
    KIND_CAPTION,
    KIND_FURNITURE,
    KIND_TABLE,
    KIND_TITLE,
    KIND_TOC,
)
from app.core.document_qa.text_shapes import PARA_TERMINALS, bracket_residual

# 表首标题要横跨表格大部分宽度, 并且贴着外框上沿。左列行号不满足宽度
_BANNER_WIDTH = 0.75
_BANNER_TOP = 0.02
_BBOX_TOL = 0.02

# 进页文本的身份。页眉页脚、公式、图片不进, 与 region_text.page_texts 同一口径
_PAGE_KINDS = frozenset({KIND_TITLE, KIND_BODY, KIND_TABLE, KIND_CAPTION, KIND_TOC})
# 一段正文最多从起始页跨到下一页。编号序列不走这段拼接。
_MAX_PAGE_SPAN = 1
# 同一页垂直间隔超过这个值且括号已平, 一般正文段就断开。括号续接忽略这个间隔。
_GAP = 0.04


@dataclass(frozen=True)
class ProseSpan:
    """一段已经续接过的正文。page 是这段的起始页。"""

    page: int
    text: str
    lines: tuple[QaLine, ...] = ()


@dataclass(frozen=True)
class GridRow:
    """同一视觉行上的网格片段。text 用空格拼单元格, 供同一行的标签和取值。

    ponytail: 一条视觉行里黏住的两个单元格仍可能被槽位一起读到。
    升级路径是按表格框裁图做单元格识别, 再按单元格角色取数。
    """

    page: int
    text: str
    lines: tuple[QaLine, ...]
    table_id: tuple
    y0: float


@dataclass
class _GridTable:
    page: int
    bbox: BBox
    rows: list[GridRow] = field(default_factory=list)

    @property
    def table_id(self) -> tuple:
        return (self.page, tuple(round(value, 3) for value in self.bbox))


@dataclass
class AuthorView:
    """一份文档的作者对象。无版面时 headings 就是原标题树, 网格为空。"""

    headings: list[QaHeading]
    prose: list[ProseSpan]
    prose_groups: list[tuple[QaLine, ...]]
    grid_rows: list[GridRow]
    grid_tables: list[_GridTable]
    captions: list[tuple[int, str]]
    title_texts: list[tuple[int, str]]
    page_texts: dict[int, str]
    bracket_spans: list[ProseSpan]


def ensure_author_view(model: DocumentModel) -> AuthorView:
    """读取或现算作者对象, 并挂到模型上。"""
    cached = model.author_view
    if isinstance(cached, AuthorView):
        return cached
    view = _build(model)
    model.author_view = view
    return view


def author_headings(model: DocumentModel) -> list[QaHeading]:
    """表外标题, 含清单句式。表内编号不在其中; 横跨表首的标题仍在。"""
    return list(ensure_author_view(model).headings)


def outline_nodes(model: DocumentModel, rules: Optional[dict] = None) -> list[QaHeading]:
    """结构、目录、重复标题、章节引用目标使用的大纲节点。"""
    from app.core.document_qa.heading_detector import is_outline_heading

    return [heading for heading in author_headings(model) if is_outline_heading(heading, rules)]


def prose_spans(model: DocumentModel) -> list[ProseSpan]:
    """正文跨度。只含紧邻行, 一段最多跨到下一页。"""
    return list(ensure_author_view(model).prose)


def bracket_spans(model: DocumentModel) -> list[ProseSpan]:
    """括号结算用的紧邻段。句号不断开, 仍不跨过表格, 最多两页。"""
    return list(ensure_author_view(model).bracket_spans)


def line_for_offset(span: ProseSpan, offset: int) -> Optional[QaLine]:
    """拼接文本里某个下标落在哪一行。没有行几何时返回 None。"""
    cursor = 0
    for line in span.lines:
        text = line.text.strip()
        if not text:
            continue
        if cursor <= offset < cursor + len(text):
            return line
        cursor += len(text)
    return span.lines[-1] if span.lines else None


def grid_rows(model: DocumentModel) -> list[GridRow]:
    """网格视觉行。无版面时为空, 表格文字仍留在旧段落里。"""
    return list(ensure_author_view(model).grid_rows)


def grid_line_texts(model: DocumentModel) -> list[tuple[int, str]]:
    """网格单行, 不把同一视觉行的单元格黏成一串。"""
    texts: list[tuple[int, str]] = []
    for row in grid_rows(model):
        for line in row.lines:
            stripped = line.text.strip()
            if stripped:
                texts.append((line.page, stripped))
    return texts


def caption_lines(model: DocumentModel) -> list[tuple[int, str]]:
    """拼好的题注, 再加上表外标题全文(附件/图题写在标题区时仍能被家族正则看见)。"""
    view = ensure_author_view(model)
    lines = list(view.captions)
    lines.extend(view.title_texts)
    for heading in view.headings:
        display = heading.display.strip()
        if display:
            lines.append((heading.page, display))
    return lines


def role_page_texts(model: DocumentModel) -> dict[int, str]:
    """角色归位后的页文本。同一行只出现一次。没有版面的页仍用旧页文本。"""
    from app.core.document_qa.region_text import page_texts

    texts = page_texts(model)
    if not model.regions:
        return texts
    for page, text in ensure_author_view(model).page_texts.items():
        texts[page] = text
    return texts


def prose_blocks(model: DocumentModel) -> list[QaBlock]:
    """版式用的正文块。有版面时一块对应一条正文跨度; 否则退回旧正文块。"""
    if not model.regions:
        from app.core.document_qa.region_text import body_blocks

        return body_blocks(model)
    blocks: list[QaBlock] = []
    for group in ensure_author_view(model).prose_groups:
        if not group:
            continue
        bbox = _union([line.bbox for line in group])
        blocks.append(QaBlock(
            page=group[0].page,
            text="".join(line.text.strip() for line in group),
            bbox=bbox,
            spans=tuple(piece for line in group for piece in line.spans),
            lines=group,
        ))
    return blocks


def grid_bracket_units(model: DocumentModel) -> list[tuple[int, str]]:
    """兼容旧调用: 返回括号段的页和全文。新代码请用 bracket_spans。"""
    return [(span.page, span.text) for span in bracket_spans(model)]


def _build(model: DocumentModel) -> AuthorView:
    if not model.regions:
        paragraphs = _legacy_paragraphs(model)
        prose = _stitch_paragraphs(paragraphs, keep_open=False)
        return AuthorView(
            headings=list(model.headings),
            prose=prose,
            prose_groups=[],
            grid_rows=[],
            grid_tables=[],
            captions=[],
            title_texts=[],
            page_texts={},
            bracket_spans=_stitch_paragraphs(paragraphs, keep_open=True),
        )
    return _build_from_regions(model)


def _legacy_paragraphs(model: DocumentModel) -> list[tuple[int, str]]:
    source = model.visual_paragraphs or model.paragraphs
    paragraphs: list[tuple[int, str]] = []
    for page in sorted(source):
        for text in source[page] or []:
            stripped = (text or "").strip()
            if stripped:
                paragraphs.append((page, stripped))
    return paragraphs


def _stitch_paragraphs(paragraphs: list[tuple[int, str]], *, keep_open: bool) -> list[ProseSpan]:
    """无几何时的续接。页码差大于 1 就断开。keep_open 时句号不断开。"""
    from app.core.document_qa.heading_detector import _match_heading

    spans: list[ProseSpan] = []
    buf: list[tuple[int, str]] = []

    def flush() -> None:
        if not buf:
            return
        spans.append(ProseSpan(page=buf[0][0], text="".join(text for _, text in buf)))
        buf.clear()

    for page, text in paragraphs:
        if buf and (page - buf[0][0] > _MAX_PAGE_SPAN or page - buf[-1][0] > 1):
            flush()
        if buf and not keep_open and _match_heading(text) is not None:
            flush()
        if buf and not keep_open:
            previous = buf[-1][1]
            opened, _closed = bracket_residual("".join(item for _, item in buf))
            if opened == 0 and previous.endswith(PARA_TERMINALS):
                flush()
        buf.append((page, text))
    flush()
    return spans


def _build_from_regions(model: DocumentModel) -> AuthorView:
    prose_lines: list[QaLine] = []
    barriers: list[QaLine] = []
    furniture_ids: set[int] = set()
    prose_groups: list[tuple[QaLine, ...]] = []
    grid_buckets: dict[tuple, list[QaLine]] = {}
    table_boxes: dict[tuple, BBox] = {}
    caption_pairs: list[tuple[int, float, str]] = []
    title_pairs: list[tuple[int, str]] = []
    title_regions: list = []
    banner_groups: list[tuple[QaLine, ...]] = []
    page_lines: list[tuple[int, float, float, str]] = []

    for page in sorted(model.regions):
        regions = model.regions[page]
        boxes = [region.bbox for region in regions if region.kind == KIND_TABLE]
        for region in regions:
            lines = tuple(sorted(region.lines, key=lambda line: (line.bbox[1], line.bbox[0])))
            if region.kind == KIND_CAPTION:
                text = "".join(line.text.strip() for line in lines)
                if text:
                    caption_pairs.append((page, lines[0].bbox[1] if lines else region.bbox[1], text))
                barriers.extend(lines)
                _remember_page_lines(page_lines, lines)
                continue
            if region.kind not in _PAGE_KINDS and region.kind != KIND_TITLE:
                for line in lines:
                    barriers.append(line)
                    if region.kind == KIND_FURNITURE:
                        furniture_ids.add(id(line))
                continue
            table = _containing(region.bbox, boxes) if region.kind != KIND_BODY else None
            if region.kind == KIND_TITLE and lines and _region_is_banner(lines, boxes):
                banner_groups.append(lines)
                title_pairs.append((page, "".join(line.text.strip() for line in lines)))
                barriers.extend(lines)
                _remember_page_lines(page_lines, lines)
                continue
            if region.kind == KIND_TITLE and table is not None:
                for line in lines:
                    _put_grid(grid_buckets, table_boxes, table, line)
                    barriers.append(line)
                _remember_page_lines(page_lines, lines)
                continue
            if region.kind == KIND_TITLE:
                title_regions.append(region)
                title_pairs.append((page, "".join(line.text.strip() for line in lines)))
                barriers.extend(lines)
                _remember_page_lines(page_lines, lines)
                continue
            kept_prose: list[QaLine] = []
            for line in lines:
                owner = _containing(line.bbox, boxes)
                if owner is not None and region.kind != KIND_TOC and _is_banner_line(line, owner):
                    banner_groups.append((line,))
                    title_pairs.append((line.page, line.text.strip()))
                    barriers.append(line)
                    _remember_page_lines(page_lines, (line,))
                    continue
                if owner is not None and region.kind != KIND_TOC:
                    _put_grid(grid_buckets, table_boxes, owner, line)
                    barriers.append(line)
                elif region.kind == KIND_BODY:
                    prose_lines.append(line)
                    kept_prose.append(line)
                else:
                    barriers.append(line)
                _remember_page_lines(page_lines, (line,))
            if kept_prose:
                prose_groups.append(tuple(kept_prose))

    headings = _compose_headings(model, title_regions, banner_groups, table_boxes)
    prose = _stitch_lines(
        prose_lines, headings, barriers, furniture_ids, keep_open=False,
    )
    bracket_prose = _stitch_lines(
        prose_lines, headings, barriers, furniture_ids, keep_open=True,
    )
    tables = _grid_tables(grid_buckets, table_boxes)
    rows = [row for table in tables for row in table.rows]
    page_texts = _join_page_lines(page_lines)
    captions = [(page, text) for page, _y, text in caption_pairs]
    view = AuthorView(
        headings=headings,
        prose=prose,
        prose_groups=prose_groups,
        grid_rows=rows,
        grid_tables=tables,
        captions=captions,
        title_texts=title_pairs,
        page_texts=page_texts,
        bracket_spans=[],
    )
    view.bracket_spans = bracket_prose + _bracket_grid_spans(tables, view)
    return view


def _remember_page_lines(sink: list[tuple[int, float, float, str]], lines: tuple[QaLine, ...]) -> None:
    for line in lines:
        stripped = line.text.strip()
        if stripped:
            sink.append((line.page, line.bbox[1], line.bbox[0], stripped))


def _join_page_lines(lines: list[tuple[int, float, float, str]]) -> dict[int, str]:
    grouped: dict[int, list[tuple[float, float, str]]] = {}
    for page, y0, x0, text in lines:
        grouped.setdefault(page, []).append((y0, x0, text))
    return {
        page: "\n".join(text for _y, _x, text in sorted(items))
        for page, items in grouped.items()
    }


def _put_grid(
    buckets: dict[tuple, list[QaLine]],
    boxes: dict[tuple, BBox],
    table: BBox,
    line: QaLine,
) -> None:
    key = (line.page, tuple(round(value, 3) for value in table))
    buckets.setdefault(key, []).append(line)
    boxes[key] = table


def _region_is_banner(lines: tuple[QaLine, ...], boxes: list[BBox]) -> bool:
    first = min(lines, key=lambda line: (line.bbox[1], line.bbox[0]))
    table = _containing(first.bbox, boxes)
    return table is not None and _is_banner_line(first, table)


def _is_banner_line(line: QaLine, table: BBox) -> bool:
    from app.core.document_qa.heading_detector import _match_heading

    width = table[2] - table[0]
    line_width = line.bbox[2] - line.bbox[0]
    if width <= 0 or line_width < width * _BANNER_WIDTH:
        return False
    if line.bbox[1] > table[1] + _BANNER_TOP:
        return False
    return _match_heading(line.text.replace("\n", " ").strip()) is not None


def _is_banner_heading(heading: QaHeading, table: BBox) -> bool:
    if not heading.bbox:
        return False
    width = table[2] - table[0]
    line_width = heading.bbox[2] - heading.bbox[0]
    if width <= 0 or line_width < width * _BANNER_WIDTH:
        return False
    if heading.bbox[1] > table[1] + _BANNER_TOP:
        return False
    return bool(heading.number or heading.title)


def _compose_headings(
    model: DocumentModel,
    title_regions: list,
    banner_groups: list[tuple[QaLine, ...]],
    table_boxes: dict[tuple, BBox],
) -> list[QaHeading]:
    boxes_by_page: dict[int, list[BBox]] = {}
    for (page, _key), box in table_boxes.items():
        boxes_by_page.setdefault(page, []).append(box)
    for page, regions in model.regions.items():
        for region in regions:
            if region.kind == KIND_TABLE:
                boxes_by_page.setdefault(page, []).append(region.bbox)

    kept: list[QaHeading] = []
    for heading in model.headings:
        box = _containing(heading.bbox, boxes_by_page.get(heading.page) or []) if heading.bbox else None
        if box is not None and not _is_banner_heading(heading, box):
            continue
        kept.append(_refresh_heading(heading, title_regions, banner_groups))

    for region in title_regions:
        synthesized = _heading_from_lines(region.page, region.lines, region.bbox)
        if synthesized is not None and not any(_same_heading(synthesized, item) for item in kept):
            kept.append(synthesized)
    for lines in banner_groups:
        if not lines:
            continue
        synthesized = _heading_from_lines(lines[0].page, lines, _union([line.bbox for line in lines]))
        if synthesized is not None and not any(_same_heading(synthesized, item) for item in kept):
            kept.append(synthesized)
    kept.sort(key=lambda heading: (heading.page, heading.bbox[1] if heading.bbox else 0, heading.display))
    return kept


def _refresh_heading(
    heading: QaHeading,
    title_regions: list,
    banner_groups: list[tuple[QaLine, ...]],
) -> QaHeading:
    lines = _lines_for_heading(heading, title_regions, banner_groups)
    if len(lines) < 2:
        return heading
    refreshed = _heading_from_lines(heading.page, lines, heading.bbox or _union([line.bbox for line in lines]))
    if refreshed is None:
        return heading
    return refreshed


def _lines_for_heading(
    heading: QaHeading,
    title_regions: list,
    banner_groups: list[tuple[QaLine, ...]],
) -> tuple[QaLine, ...]:
    if heading.bbox is None:
        return ()
    for region in title_regions:
        if region.page == heading.page and (
            _bbox_close(heading.bbox, region.bbox) or _center_in_box(heading.bbox, region.bbox)
        ):
            return tuple(region.lines)
    for lines in banner_groups:
        if lines and lines[0].page == heading.page and _center_in_box(heading.bbox, _union([line.bbox for line in lines])):
            return lines
    return ()


def _heading_from_lines(page: int, lines: tuple[QaLine, ...] | list[QaLine], bbox: BBox) -> Optional[QaHeading]:
    from app.core.document_qa.heading_detector import _match_heading

    ordered = tuple(sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0])))
    text = "".join(line.text.strip() for line in ordered)
    matched = _match_heading(text.replace("\n", " "))
    if matched is None:
        return None
    number, title, level, kind = matched
    return QaHeading(page=page, level=level, number=number, title=title, bbox=bbox, kind=kind)


def _same_heading(left: QaHeading, right: QaHeading) -> bool:
    if left.page != right.page:
        return False
    if left.bbox and right.bbox and _bbox_close(left.bbox, right.bbox):
        return True
    folded_left = "".join(left.display.split())
    folded_right = "".join(right.display.split())
    if not folded_left or not folded_right:
        return False
    return folded_left.startswith(folded_right) or folded_right.startswith(folded_left)


def _line_key(line: QaLine) -> tuple[int, float, float]:
    return (line.page, line.bbox[1], line.bbox[0])


def _separated(
    previous: QaLine,
    current: QaLine,
    barriers: list[QaLine],
    furniture_ids: set[int],
) -> bool:
    """中间跳过了表格、标题等就不是紧邻。换页时只隔着页眉页脚仍算紧邻。"""
    if current.page - previous.page > 1 or current.page < previous.page:
        return True
    left = _line_key(previous)
    right = _line_key(current)
    crossed = [item for item in barriers if left < _line_key(item) < right]
    if not crossed:
        return False
    if current.page == previous.page + 1 and all(id(item) in furniture_ids for item in crossed):
        return False
    return True


def _stitch_lines(
    lines: list[QaLine],
    headings: list[QaHeading],
    barriers: list[QaLine],
    furniture_ids: set[int],
    *,
    keep_open: bool,
) -> list[ProseSpan]:
    """只拼接紧邻行。keep_open 时句号和垂直间隔不断开, 供括号跨段跨页。"""
    ordered = sorted((line for line in lines if line.text.strip()), key=_line_key)
    spans: list[ProseSpan] = []
    buf: list[QaLine] = []

    def flush() -> None:
        if not buf:
            return
        text = "".join(line.text.strip() for line in buf)
        if text:
            spans.append(ProseSpan(page=buf[0].page, text=text, lines=tuple(buf)))
        buf.clear()

    for line in ordered:
        if buf and (
            line.page - buf[0].page > _MAX_PAGE_SPAN
            or _separated(buf[-1], line, barriers, furniture_ids)
            or (not keep_open and _heading_between(buf[-1], line, headings))
        ):
            flush()
        if buf and not keep_open:
            opened, _closed = bracket_residual("".join(item.text.strip() for item in buf))
            previous = buf[-1].text.strip()
            same_page_gap = (
                line.page == buf[-1].page and line.bbox[1] - buf[-1].bbox[3] > _GAP
            )
            if opened == 0 and (previous.endswith(PARA_TERMINALS) or same_page_gap):
                flush()
        buf.append(line)
    flush()
    return spans


def _bracket_grid_spans(tables: list[_GridTable], view: AuthorView) -> list[ProseSpan]:
    """表格里只把紧邻的行接在一起算括号。栈已空就收束, 不把整表拼成一条摘录。"""
    spans: list[ProseSpan] = []
    buf: list[QaLine] = []
    previous: Optional[_GridTable] = None
    origin_page: Optional[int] = None

    def flush() -> None:
        nonlocal origin_page
        if not buf:
            origin_page = None
            return
        text = "".join(line.text.strip() for line in buf if line.text.strip())
        if text:
            spans.append(ProseSpan(page=buf[0].page, text=text, lines=tuple(buf)))
        buf.clear()
        origin_page = None

    for table in tables:
        if previous is not None and buf and _table_blocked(previous, table, view):
            flush()
        for row in table.rows:
            row_lines = [line for line in row.lines if line.text.strip()] or []
            if not row_lines:
                continue
            if origin_page is not None and row.page - origin_page > _MAX_PAGE_SPAN:
                flush()
            if origin_page is None:
                origin_page = row.page
            buf.extend(row_lines)
            opened, extra = bracket_residual("".join(line.text.strip() for line in buf))
            if opened == 0 and extra == 0:
                flush()
        previous = table
    flush()
    return spans


def _heading_between(previous: QaLine, current: QaLine, headings: list[QaHeading]) -> bool:
    for heading in headings:
        y0 = heading.bbox[1] if heading.bbox else None
        if y0 is None:
            if previous.page < heading.page < current.page:
                return True
            continue
        if previous.page == current.page == heading.page and previous.bbox[3] <= y0 <= current.bbox[1]:
            return True
        if previous.page < heading.page < current.page:
            return True
        if previous.page < current.page and heading.page == current.page and y0 <= current.bbox[1]:
            return True
    return False


def _grid_tables(
    buckets: dict[tuple, list[QaLine]],
    boxes: dict[tuple, BBox],
) -> list[_GridTable]:
    tables: list[_GridTable] = []
    for key in sorted(buckets, key=lambda item: (item[0], boxes[item][1], boxes[item][0])):
        page = key[0]
        box = boxes[key]
        table = _GridTable(page=page, bbox=box)
        for group in _row_groups(buckets[key]):
            parts = [line.text.strip() for line in sorted(group, key=lambda line: line.bbox[0])]
            text = " ".join(part for part in parts if part)
            if not text:
                continue
            table.rows.append(GridRow(
                page=page,
                text=text,
                lines=tuple(group),
                table_id=table.table_id,
                y0=min(line.bbox[1] for line in group),
            ))
        if table.rows:
            tables.append(table)
    tables.sort(key=lambda table: (table.page, table.bbox[1], table.bbox[0]))
    return tables


def _row_groups(lines: list[QaLine]) -> list[list[QaLine]]:
    rows: list[list[QaLine]] = []
    for line in sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0])):
        center = (line.bbox[1] + line.bbox[3]) / 2
        if rows:
            top = min(item.bbox[1] for item in rows[-1])
            bottom = max(item.bbox[3] for item in rows[-1])
            if top <= center <= bottom:
                rows[-1].append(line)
                continue
        rows.append([line])
    return rows


def _table_blocked(previous: _GridTable, current: _GridTable, view: AuthorView) -> bool:
    for heading in view.headings:
        y0 = heading.bbox[1] if heading.bbox else None
        if previous.page < heading.page < current.page:
            return True
        if y0 is None:
            continue
        if previous.page == current.page == heading.page and previous.bbox[3] <= y0 <= current.bbox[1]:
            return True
        if previous.page < current.page and heading.page == current.page and y0 <= current.bbox[1]:
            return True
    for span in view.prose:
        if not span.lines:
            if previous.page < span.page < current.page:
                return True
            continue
        y0 = span.lines[0].bbox[1]
        page = span.lines[0].page
        if previous.page < page < current.page:
            return True
        if previous.page == current.page == page and previous.bbox[3] <= y0 <= current.bbox[1]:
            return True
        if previous.page < current.page and page == current.page and y0 <= current.bbox[1]:
            return True
    return False


def _containing(bbox: Optional[BBox], boxes: list[BBox]) -> Optional[BBox]:
    if bbox is None:
        return None
    hits = [box for box in boxes if _center_in_box(bbox, box)]
    if not hits:
        return None
    return min(hits, key=lambda box: abs((box[2] - box[0]) * (box[3] - box[1])))


def _center_in_box(inner: BBox, outer: BBox) -> bool:
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _bbox_close(left: BBox, right: BBox, tol: float = _BBOX_TOL) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(left, right))


def _union(boxes: list[BBox]) -> BBox:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
