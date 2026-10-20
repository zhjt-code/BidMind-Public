"""
职责: 从文档块/正文行识别标题树(编号正则 + 字号/字重信号 + 行形态)
不做: 不判断标题业务含义、不建目录规格、不 import writer 或 app.api
"""

from __future__ import annotations

import re
import statistics
from typing import Optional

from app.core.document_qa.contracts import BBox, DocumentModel, QaBlock, QaHeading, QaPage
from app.core.document_qa.text_shapes import is_index_or_leader_line

# 标题编号句式(行首锚定); 捕获组1=编号, 捕获组2=标题文本
_HEADING_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 第X章/节/部分/篇/卷
    (re.compile(r"^(第[一二三四五六七八九十百\d]+[章节部分篇卷])\s*(.{0,40})$"), "unit"),
    # 多级数字编号 3.1 / 3.1.2 (至少一个点, 避免误吞普通列表)
    (re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,4})[\s、.．]?\s*(.{2,40})$"), "decimal"),
    # 一级数字编号 3、/ 3.
    (re.compile(r"^(\d{1,2})[、.．]\s*(.{2,40})$"), "top_digit"),
    # 顶级数字 + 空格 + 标题("1 项目理解与总体目标"; 无分隔符,
    # 仅在字号/字重强信号下接受, 防表格序号列误报, 见 _STRICT_KINDS)
    (re.compile(r"^(\d{1,2})\s+([\u4e00-\u9fff].{1,30})$"), "top_space"),
    # 附件/附录标题("附件2 数据字段说明"; 同为强信号门槛)
    (re.compile(r"^(附[件录]\s*(?:[0-9]{1,2}|[一二三四五六七八九十]{1,3}))\s+(.{2,30})$"), "attachment"),
    # 中文序号 一、
    (re.compile(r"^([一二三四五六七八九十]{1,3})[、.．]\s*(.{2,40})$"), "cn_digit"),
    # 括号序号 （一） / (1)
    (re.compile(r"^[（(]([一二三四五六七八九十\d]{1,3})[)）]\s*(.{2,40})$"), "paren"),
]

# 阿拉伯数字编号/弱句式必须有字号/字重强信号才接受; 降级(无span)路径不启用
# top_digit 与 top_space 同形态: 正文清单 "1、/1." 不能靠短行豁免入树
_STRICT_KINDS = frozenset({"top_space", "top_digit", "attachment"})
_ARABIC_LIST_KINDS = frozenset({"top_digit", "top_space"})
_SENIOR_KINDS = frozenset({"unit", "decimal", "cn_digit"})
_DEFAULT_OUTLINE_KINDS = frozenset({
    "unit", "decimal", "cn_digit", "attachment", "top_space", "top_digit",
})

# 标题文本不应以句读收尾(整句正文误配的最强信号)
_SENTENCE_END = ("。", "；", "，", ";", ",", "：", ":")
# 标题正文中出现这些标点, 说明行内已从标题切到句子
_TITLE_SENTENCE_MARKS = ("。", "！", "？", ";", "!")
_TITLE_CLAUSE_MARKS = ("，", ",")
# 计量标题前缀: 「82万元」「6台」「30%」里单位前面的数字
_MEASURE_HEAD_RE = re.compile(r"^[\d.,]+%?$")

_BOLD_FLAG = 1 << 4  # fitz span flags bit: bold
_LEFTOVER_MIN = 20  # 编号标题后折叠余量, 短单元格说明不构成混排

# 同 span 混排: 标题为无空白短词, 后接空白与正文余量(避免 .{2,40} 吞掉后文)
_PREFIX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(第[一二三四五六七八九十百\d]+[章节部分篇卷])\s*([\u4e00-\u9fffA-Za-z0-9]{0,40})\s+(.+)$"), "unit"),
    (re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,4})[\s、.．]?\s*([\u4e00-\u9fffA-Za-z0-9]{2,40})\s+(.+)$"), "decimal"),
    (re.compile(r"^(\d{1,2})[、.．]\s*([\u4e00-\u9fffA-Za-z0-9]{2,40})\s+(.+)$"), "top_digit"),
    (re.compile(r"^(\d{1,2})\s+([\u4e00-\u9fff]{2,30})\s+(.+)$"), "top_space"),
    (re.compile(r"^(附[件录]\s*(?:[0-9]{1,2}|[一二三四五六七八九十]{1,3}))\s+([\u4e00-\u9fffA-Za-z0-9]{2,30})\s+(.+)$"), "attachment"),
    (re.compile(r"^([一二三四五六七八九十]{1,3})[、.．]\s*([\u4e00-\u9fffA-Za-z0-9]{2,40})\s+(.+)$"), "cn_digit"),
    (re.compile(r"^[（(]([一二三四五六七八九十\d]{1,3})[)）]\s*([\u4e00-\u9fffA-Za-z0-9]{2,40})\s+(.+)$"), "paren"),
]


def _level_for(kind: str, number: str) -> int:
    if kind == "unit":
        return 1 if ("章" in number or "部分" in number or "篇" in number or "卷" in number) else 2
    if kind == "decimal":
        return number.count(".") + 1
    if kind in ("top_digit", "cn_digit", "top_space", "attachment"):
        return 1
    return 2  # paren 序号按二级


def _heading_title_clause(title: str) -> Optional[str]:
    """句读切开时只留标题前缀; 逗号从句整段丢掉。

    句号后折叠余量足够时, 前缀是标题、余量是正文; 余量不足则整行仍是句子。
    逗号检查作用在切开后的标题上, 不把正文里的逗号当成否决。
    """
    if not title:
        return title
    for index, char in enumerate(title):
        if char not in _TITLE_SENTENCE_MARKS:
            continue
        leftover = "".join(title[index + 1:].split())
        if len(leftover) < _LEFTOVER_MIN:
            return None
        title = title[:index].strip()
        if len(title) < 2:
            return None
        break
    if any(mark in title for mark in _TITLE_CLAUSE_MARKS):
        return None
    if any(mark in title for mark in _TITLE_SENTENCE_MARKS):
        return None
    return title


def _match_heading_raw(line: str) -> Optional[tuple[str, str, int, str]]:
    """句式匹配, 不含句读/长度否决。返回 (编号, 标题, level, kind)。"""
    text = line.strip()
    if not text:
        return None
    for pattern, kind in _HEADING_PATTERNS:
        matched = pattern.match(text)
        if not matched:
            continue
        number = matched.group(1).strip()
        title = (matched.group(2) or "").strip()
        if kind == "unit" and not title:
            title = ""
        elif len(title) < 2:
            continue
        title = _heading_title_clause(title)
        if title is None:
            continue
        return number, title, _level_for(kind, number), kind
    return None


def _match_heading(line: str) -> Optional[tuple[str, str, int, str]]:
    """返回 (编号, 标题, level, kind); 不命中返回 None。"""
    text = line.strip()
    if not text or len(text) > 50:
        return None
    if text.endswith(_SENTENCE_END):
        return None
    return _match_heading_raw(text)


def _body_font_size(blocks: list[QaBlock]) -> Optional[float]:
    sizes = [
        round(span.size, 1)
        for block in blocks
        for span in block.spans
        if span.text.strip()
    ]
    if len(sizes) < 20:
        return None
    return statistics.median(sizes)


def _has_strong_signal(span, body_size: Optional[float]) -> bool:
    if body_size is None:
        return False
    return span.size >= body_size + 0.5 or bool(span.flags & _BOLD_FLAG)


def _try_inline_heading(block: QaBlock, body_size: Optional[float]) -> Optional[QaHeading]:
    """行内混排标题。

    两条形态共用准入: (1) 首 span 编号句式+强信号、后续 span 回落正文;
    (2) 同 span/同行编号前缀后仍有足够正文余量。弱句式仍要强信号。
    """
    styled = _try_style_split_heading(block, body_size)
    if styled is not None:
        return styled
    return _try_remainder_heading(block, body_size)


def _try_style_split_heading(block: QaBlock, body_size: Optional[float]) -> Optional[QaHeading]:
    if body_size is None or len(block.spans) < 2:
        return None
    first = block.spans[0]
    matched = _match_heading(first.text)
    if matched is None or not _has_strong_signal(first, body_size):
        return None
    follower = next((span for span in block.spans[1:] if span.text.strip()), None)
    if follower is None:
        return None
    if follower.size > body_size + 0.2 or follower.size >= first.size:
        return None
    number, title, level, kind = matched
    return QaHeading(
        page=block.page,
        level=level,
        number=number,
        title=title,
        bbox=first.bbox,
        kind=kind,
    )


def _folded_len(text: str) -> int:
    return len("".join(text.split()))


def _match_heading_prefix(text: str) -> Optional[tuple[str, str, int, str]]:
    """整行过长时取编号句式前缀; 余量不足则不是混排。"""
    raw = text.strip()
    if not raw:
        return None
    for pattern, kind in _PREFIX_PATTERNS:
        matched = pattern.match(raw)
        if not matched:
            continue
        leftover = matched.group(3) if matched.lastindex and matched.lastindex >= 3 else ""
        if _folded_len(leftover) < _LEFTOVER_MIN:
            continue
        number = matched.group(1).strip()
        title = (matched.group(2) or "").strip()
        if kind == "unit" and not title:
            title = ""
        elif len(title) < 2:
            continue
        if title.endswith(_SENTENCE_END):
            continue
        title = _heading_title_clause(title)
        if title is None:
            continue
        return number, title, _level_for(kind, number), kind
    return None


def _prefix_bbox(block: QaBlock, prefix: str) -> Optional[BBox]:
    """ponytail: 按字符占比切首 span 宽度, 无 glyph 宽度; 升级 fitz 字宽。"""
    span = block.spans[0] if block.spans else None
    if span is None:
        return block.bbox
    full = (span.text or "").strip() or (block.text or "").strip()
    if not full:
        return span.bbox
    ratio = min(0.85, max(0.12, len(prefix.strip()) / max(len(full), 1)))
    x0, y0, x1, y1 = span.bbox
    return (x0, y0, x0 + (x1 - x0) * ratio, y1)


def _try_remainder_heading(block: QaBlock, body_size: Optional[float]) -> Optional[QaHeading]:
    first_line = (block.text.split("\n", 1)[0] or "").strip()
    parsed = _match_heading_prefix(first_line)
    if parsed is None:
        return None
    number, title, level, kind = parsed
    if kind in _STRICT_KINDS:
        span = block.spans[0] if block.spans else None
        if span is None or not _has_strong_signal(span, body_size):
            return None
    prefix = f"{number} {title}".strip()
    return QaHeading(
        page=block.page,
        level=level,
        number=number,
        title=title,
        bbox=_prefix_bbox(block, prefix),
        kind=kind,
    )


def _heading_cfg(rules: Optional[dict]) -> dict:
    return (rules or {}).get("heading") or {}


def outline_kind_set(rules: Optional[dict] = None) -> frozenset[str]:
    """大纲句式集合: 结构检查只消费这些 kind; 清单句式不在其列。"""
    items = _heading_cfg(rules).get("outline_kinds")
    if items:
        return frozenset(str(item) for item in items if str(item).strip())
    return _DEFAULT_OUTLINE_KINDS


def is_outline_heading(heading: QaHeading, rules: Optional[dict] = None) -> bool:
    """未标注 kind 的存量夹具视为大纲, 避免旧测试失去结构检查。"""
    kind = heading.kind or ""
    if not kind:
        return True
    return kind in outline_kind_set(rules)


def _unit_titles(rules: Optional[dict]) -> set[str]:
    return {str(item).strip() for item in _heading_cfg(rules).get("unit_titles") or () if str(item).strip()}


def _title_is_unit_measure(title: str, units: set[str]) -> bool:
    """标题整词是单位, 或数字粘连单位, 都是计量不是章节名。"""
    text = (title or "").strip()
    if not text:
        return False
    if text in units:
        return True
    for unit in sorted((item for item in units if item), key=len, reverse=True):
        if not text.endswith(unit):
            continue
        head = text[: -len(unit)].strip()
        if head and _MEASURE_HEAD_RE.fullmatch(head):
            return True
    return False


def _scan_label_max_chars(rules: Optional[dict]) -> int:
    try:
        return int(_heading_cfg(rules).get("scan_label_max_chars") or 160)
    except (TypeError, ValueError):
        return 160


def _page_meta(model: DocumentModel) -> dict[int, QaPage]:
    return {page.page_num: page for page in model.pages}


def _number_value(number: str) -> Optional[int]:
    token = (number or "").strip()
    if token.isdigit():
        return int(token)
    try:
        from pycnnum import cn2num

        value = cn2num(token)
        return int(value) if value else None
    except Exception:  # noqa: BLE001
        return None


def _page_has_senior_heading(page_lines: list[str]) -> bool:
    """页内已有章/节/中文序号时, 阿拉伯数字行是节内清单而不是新的一级标题。"""
    for line in page_lines:
        raw = _match_heading(line)
        if raw is not None and raw[3] in _SENIOR_KINDS:
            return True
    return False


def _sibling_list_vetoed(
    first_line: str,
    matched: tuple[str, str, int, str],
    page_lines: list[str],
) -> bool:
    """同页同句式连续编号视为清单: 同胞因句读被拒, 或阿拉伯数字连续号。"""
    number, _title, _level, kind = matched
    value = _number_value(number)
    if value is None:
        return False
    for line in page_lines:
        if line == first_line:
            continue
        sentence_rejected = line.endswith(_SENTENCE_END)
        raw = _match_heading_raw(line.rstrip("。；，;,:：")) if sentence_rejected else _match_heading(line)
        if raw is None:
            raw = _match_heading_raw(line)
        if raw is None or raw[3] != kind:
            continue
        other_value = _number_value(raw[0])
        if other_value is None or abs(other_value - value) != 1:
            continue
        if sentence_rejected or kind in _ARABIC_LIST_KINDS:
            return True
    return False


def _should_veto(
    model: DocumentModel,
    page: int,
    first_line: str,
    matched: tuple[str, str, int, str],
    page_lines: list[str],
    rules: Optional[dict],
) -> bool:
    number, title, _level, kind = matched
    if page in model.toc_zone:
        return True
    if is_index_or_leader_line(first_line):
        return True
    if _title_is_unit_measure(title, _unit_titles(rules)):
        return True
    if kind in _ARABIC_LIST_KINDS and _page_has_senior_heading(page_lines):
        return True
    meta = _page_meta(model).get(page)
    if (
        meta is not None
        and meta.image_count > 0
        and meta.char_count < _scan_label_max_chars(rules)
        and kind in _STRICT_KINDS | {"top_digit"}
    ):
        return True
    if _sibling_list_vetoed(first_line, matched, page_lines):
        return True
    return False


def _page_first_lines(blocks: list[QaBlock], page: int) -> list[str]:
    return [
        (block.text.split("\n", 1)[0] or "").strip()
        for block in blocks
        if block.page == page
    ]


def detect_headings(model: DocumentModel, rules: Optional[dict] = None) -> list[QaHeading]:
    """识别正文标题。

    优先用 fitz blocks(有字号/字重信号); blocks 缺失(降级)时回退正文行形态判定。
    句式只产生候选, 目录/清单/扫描标签/计量单位由否决层拒绝。
    """
    headings: list[QaHeading] = []

    if model.blocks:
        body_size = _body_font_size(model.blocks)
        for block in model.blocks:
            first_line = (block.text.split("\n", 1)[0] or "").strip()
            page_lines = _page_first_lines(model.blocks, block.page)
            matched = _match_heading(first_line)
            if matched is None:
                inline = _try_inline_heading(block, body_size)
                if inline is None:
                    continue
                inline_matched = (inline.number or "", inline.title, inline.level, inline.kind or "")
                leftover = first_line[len(f"{inline.number} {inline.title}".strip()):]
                if is_index_or_leader_line(first_line) or is_index_or_leader_line(leftover):
                    continue
                if _should_veto(model, block.page, first_line, inline_matched, page_lines, rules):
                    continue
                headings.append(inline)
                continue
            number, title, level, kind = matched
            if body_size is not None and block.spans:
                span = block.spans[0]
                strong_signal = _has_strong_signal(span, body_size)
                if kind in _STRICT_KINDS and not strong_signal:
                    continue
            elif kind in _STRICT_KINDS:
                continue
            if _should_veto(model, block.page, first_line, matched, page_lines, rules):
                continue
            headings.append(QaHeading(
                page=block.page,
                level=level,
                number=number,
                title=title,
                bbox=block.bbox,
                kind=kind,
            ))
        return headings

    for page_num in sorted(model.body_texts):
        lines = [(line or "").strip() for line in (model.body_texts[page_num] or "").split("\n")]
        for line in lines:
            matched = _match_heading(line)
            if not matched:
                continue
            number, title, level, kind = matched
            if kind in _STRICT_KINDS:
                continue
            if len(line) > 40:
                continue
            if _should_veto(model, page_num, line, matched, lines, rules):
                continue
            headings.append(QaHeading(
                page=page_num, level=level, number=number, title=title, kind=kind,
            ))
    return headings
