"""
职责: 用载体无关信号识别可独立废标/无效投标的原子条件。
不做: 不按表/列表/段落分通道、不调用 LLM、不访问 DB。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.core.extractor.mandatory.contracts import (
    MandatorySignal,
    PageText,
    ScopeWindow,
)
from app.core.extractor.tender_extractor_parts.dedup_normalize import (
    normalize_dedup_key,
    numeric_fingerprint,
)

SCOPE_HEADING_TERMS: tuple[str, ...] = (
    "否决项",
    "否决条款",
    "否决投标",
    "废标条款",
    "废标条件",
    "投标无效",
    "无效投标",
    "资格审查",
    "资格预审",
    "响应性评审",
    "符合性审查",
    "符合性评审",
    "实质性要求",
    "实质性条款",
    "投标人须知前附表",
)

CONSEQUENCE_LEADS: tuple[str, ...] = (
    "有下列情形之一",
    "出现下列情形",
    "存在下列情况",
    "下列情形之一",
)

CONSEQUENCE_PHRASES: tuple[str, ...] = (
    "否则废标",
    "否则投标无效",
    "作为废标处理",
    "按废标处理",
    "作废标处理",
    "否决其投标",
    "否决投标",
    "投标无效",
    "无效投标",
    "按无效投标处理",
)

_SCORING_HEADING_TERMS: tuple[str, ...] = (
    "评分标准",
    "评分办法",
    "评标办法",
    "评分细则",
    "评分因素",
)

_STAR_RE = re.compile(r"[★▲☆※]")
_NUMBERED_LINE_RE = re.compile(
    r"^\s*(?:\(?\d+\)|[（(]\d+[）)]|\d+[.、．]|[①②③④⑤⑥⑦⑧⑨⑩]|[一二三四五六七八九十]+、)"
)
_TABLE_ORDINAL_RE = re.compile(r"^\s*\|+\s*\d+\s*\|")
_HEADING_LINE_MAX = 48
_SKIP_CHUNK_TYPES = frozenset({"image_evidence", "image_annotation"})


def looks_like_atomic_disqualifier(text: str | None) -> bool:
    """判定一段文字是否像可独立触发废标/无效投标的条件。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    if _STAR_RE.search(raw) and not _is_star_legend(raw):
        return True
    return any(phrase in raw for phrase in CONSEQUENCE_PHRASES)


def atomic_conditions_must_stay_apart(left: str, right: str) -> bool:
    """两条高风险条件的数字事实不同时禁止合并。: 否决谓词只能落在模型无法改写的轴上。此前按「归一化文本不同」
 判定，凡带废标短语的两句改写（「…作废标处理」）一律禁并，同一条件的多处
 表述再也合不回去；条件是否相同由合并决策判断，这里只守数字指纹。
 """
    if not looks_like_atomic_disqualifier(left) and not looks_like_atomic_disqualifier(right):
        return False
    left_numbers = numeric_fingerprint(left)
    right_numbers = numeric_fingerprint(right)
    if not left_numbers or not right_numbers:
        return False
    return set(left_numbers) != set(right_numbers)


def line_starts_enumeration(text: str | None) -> bool:
    """首个非空行是否为编号列表项（列表续项 / 废标列举的可枚举身份）。"""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return bool(_NUMBERED_LINE_RE.match(stripped))
    return False


def pages_from_chunks(chunks: Sequence[object]) -> list[PageText]:
    """把分块按页拼接。图片证据块跳过，表行/整表/正文同等对待。"""
    by_page: dict[int, list[str]] = {}
    section_by_page: dict[int, str] = {}
    for chunk in chunks:
        chunk_type = str(getattr(chunk, "chunk_type", "") or "")
        if chunk_type in _SKIP_CHUNK_TYPES:
            continue
        page_num = getattr(chunk, "page_num", None)
        if not isinstance(page_num, int):
            continue
        text = str(getattr(chunk, "content", "") or "").strip()
        if not text:
            continue
        by_page.setdefault(page_num, []).append(text)
        section = str(getattr(chunk, "section_path", "") or "").strip()
        if section and page_num not in section_by_page:
            section_by_page[page_num] = section
    return [
        PageText(
            page_num=page,
            text="\n".join(by_page[page]),
            section_path=section_by_page.get(page, ""),
        )
        for page in sorted(by_page)
    ]


def locate_high_stakes_windows(pages: Sequence[PageText]) -> tuple[ScopeWindow, ...]:
    """定位高风险作用域页窗（专章/前附表/后果引导段及其续页）。"""
    in_scope: dict[int, str] = {}
    by_num = {page.page_num: page for page in pages}
    for page in pages:
        reason = _scope_reason(page)
        if reason:
            in_scope[page.page_num] = reason
    for page in pages:
        if page.page_num in in_scope:
            continue
        prev = by_num.get(page.page_num - 1)
        if prev is None or prev.page_num not in in_scope:
            continue
        if _page_continues_list(page):
            in_scope[page.page_num] = "list_continuation"

    windows: list[ScopeWindow] = []
    current: list[int] = []
    current_reason = ""
    for page_num in sorted(in_scope):
        if current and page_num != current[-1] + 1:
            windows.append(ScopeWindow(pages=tuple(current), reason=current_reason))
            current = []
        if not current:
            current_reason = in_scope[page_num]
        current.append(page_num)
    if current:
        windows.append(ScopeWindow(pages=tuple(current), reason=current_reason))
    return tuple(windows)


def extend_windows_with_scattered_signals(
    windows: tuple[ScopeWindow, ...],
    signals: Sequence[MandatorySignal],
) -> tuple[ScopeWindow, ...]:
    """专章外的★/后果散句所在页并入抽取窗，避免只扫专章。"""
    covered = {page for window in windows for page in window.pages}
    extra = sorted(
        {
            signal.page_num
            for signal in signals
            if signal.kind in {"star", "consequence_line", "numbered_condition"}
            and signal.page_num not in covered
        }
    )
    if not extra:
        return windows
    return windows + (ScopeWindow(pages=tuple(extra), reason="scattered_signal"),)


def harvest_mandatory_signals(pages: Sequence[PageText]) -> tuple[MandatorySignal, ...]:
    """全文收割星标、后果句、编号废标条件；不限是否落在专章。"""
    signals: list[MandatorySignal] = []
    for page in pages:
        in_high_stakes = _scope_reason(page) is not None
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        in_list = False
        for line in lines:
            if any(lead in line for lead in CONSEQUENCE_LEADS):
                in_list = True
                signals.append(
                    MandatorySignal(page_num=page.page_num, kind="consequence_lead", snippet=line[:180])
                )
                continue
            if _is_enumerable_condition_line(line, in_list=in_list, in_high_stakes=in_high_stakes):
                signals.append(
                    MandatorySignal(page_num=page.page_num, kind="numbered_condition", snippet=line[:180])
                )
                continue
            if in_list and _looks_like_heading(line):
                in_list = False
            if _STAR_RE.search(line) and not _is_star_legend(line):
                signals.append(
                    MandatorySignal(page_num=page.page_num, kind="star", snippet=line[:180])
                )
                continue
            if looks_like_atomic_disqualifier(line) and not _looks_like_heading(line):
                signals.append(
                    MandatorySignal(page_num=page.page_num, kind="consequence_line", snippet=line[:180])
                )
    return _dedupe_signals(tuple(signals))


def list_enumerable_anchors(
    pages: Sequence[PageText], window: ScopeWindow
) -> tuple[str, ...]:
    """作用域内可枚举锚原文：编号项 + 非图例星标行。"""
    wanted = set(window.pages)
    anchors: list[str] = []
    for page in pages:
        if page.page_num not in wanted:
            continue
        in_list = False
        for line in page.text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if any(lead in stripped for lead in CONSEQUENCE_LEADS):
                in_list = True
                continue
            if _is_enumerable_condition_line(
                stripped,
                in_list=in_list,
                in_high_stakes=True,
            ):
                anchors.append(stripped)
                continue
            if in_list and _looks_like_heading(stripped):
                in_list = False
            if _STAR_RE.search(stripped) and not _is_star_legend(stripped):
                anchors.append(stripped)
    return tuple(anchors)


def count_enumerable_anchors(pages: Sequence[PageText], window: ScopeWindow) -> int:
    """作用域内可枚举锚数量：编号项 + 非图例星标行。"""
    return len(list_enumerable_anchors(pages, window))


def _scope_reason(page: PageText) -> str | None:
    if _is_scoring_primary(page):
        return None
    section = page.section_path or ""
    if any(term in section for term in SCOPE_HEADING_TERMS):
        return "section_path"
    if _has_scope_heading(page.text):
        return "heading"
    if any(lead in page.text for lead in CONSEQUENCE_LEADS):
        return "consequence_block"
    return None


def _has_scope_heading(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) > _HEADING_LINE_MAX:
            continue
        if any(term in stripped for term in SCOPE_HEADING_TERMS):
            return True
    return False


def _is_scoring_primary(page: PageText) -> bool:
    has_scoring_heading = False
    has_mandatory_heading = False
    for line in (page.section_path, *page.text.splitlines()):
        stripped = (line or "").strip()
        if not stripped or len(stripped) > _HEADING_LINE_MAX:
            continue
        if any(term in stripped for term in _SCORING_HEADING_TERMS):
            has_scoring_heading = True
        if any(term in stripped for term in SCOPE_HEADING_TERMS):
            has_mandatory_heading = True
    return has_scoring_heading and not has_mandatory_heading


def _is_enumerable_condition_line(
    line: str,
    *,
    in_list: bool,
    in_high_stakes: bool,
) -> bool:
    """编号列举与高风险页表内序号是同一类可枚举身份，不是表专用解析。"""
    if in_list and _NUMBERED_LINE_RE.match(line):
        return True
    return in_high_stakes and bool(_TABLE_ORDINAL_RE.match(line))


def _page_continues_list(page: PageText) -> bool:
    return line_starts_enumeration(page.text)


def _looks_like_heading(line: str) -> bool:
    return len(line) <= _HEADING_LINE_MAX and any(
        term in line for term in (*SCOPE_HEADING_TERMS, *_SCORING_HEADING_TERMS)
    )


def _is_star_legend(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) > 24:
        return False
    return any(
        token in compact
        for token in (
            "带★",
            "标有★",
            "★号为",
            "★为实质性",
            "★号条款为",
            "表示实质性",
        )
    )


def _dedupe_signals(signals: tuple[MandatorySignal, ...]) -> tuple[MandatorySignal, ...]:
    seen: set[tuple[int, str, str]] = set()
    unique: list[MandatorySignal] = []
    for signal in signals:
        key = (signal.page_num, signal.kind, normalize_dedup_key(signal.snippet))
        if key in seen:
            continue
        seen.add(key)
        unique.append(signal)
    return tuple(unique)
