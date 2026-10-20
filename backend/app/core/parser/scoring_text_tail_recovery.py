"""段落化评分尾页恢复。

职责: 在评分区段内把紧邻评分表后的段落化评分尾行恢复为 scoring_table markdown 行。
不做: 不解析表格列、不做评分项落库、不调用 LLM/DB。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.parser.pdf_parser import ParsedDocument

_NON_SCORING_HEADING_RE = re.compile(
    r"(?:^|[。；;])\s*[一二三四五六七八九十0-9]{1,3}[、.．]?\s*"
    r"(?:响应文件的澄清|出现下列情形|评审结果|合同授予|其他事项|附则)"
)
_SCORE_RE = re.compile(r"(?<![~～/至到-])(\d+(?:\.\d+)?)\s*分")
_ROW_STYLE_RE = re.compile(
    r"(?:^|\s)[一二三四五六七八九十0-9]{1,3}\s+"
    r"([\u4e00-\u9fffA-Za-z0-9、，和及]{2,40}?)\s+"
    r"(\d+(?:\.\d+)?)\s*分"
)
_JOINED_NAME_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9]{2,20}"
    r"(?:措施|方案|服务|项目|团队|要求|能力|覆盖|保障)"
    r"(?:和|及|、)"
    r"[\u4e00-\u9fffA-Za-z0-9]{2,20}"
    r"(?:措施|方案|服务|项目|团队|要求|能力|覆盖|保障))"
)
_NAME_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9]{2,24}"
    r"(?:措施|方案|服务|项目|团队|要求|能力|覆盖|保障))"
)
_SCORE_ACTION_TERMS = ("得", "不得分", "打分", "评分", "合理打分", "仅提供承诺不得分")
_VOLUME_MARKERS = ("报价部分", "价格部分", "商务部分", "综合部分", "技术部分")


@dataclass(frozen=True)
class TextScoreTailRow:
    """段落化尾页中恢复出的单条评分行。"""

    page_num: int
    group: str
    name: str
    score: float
    rule: str


def append_trailing_text_score_rows(
    markdown: str,
    parsed_doc: "ParsedDocument",
    section: Any,
    table_page_end: int,
) -> str:
    """把紧邻 scoring_table 后的段落化评分尾行追加到 markdown。

    只扫描同一评分区段内、当前物理表之后的连续无表格页；遇到下一张表停止。
    这避免从任意普通段落中猜评分项，同时覆盖 PDF 表格尾行退化为 paragraph 的形态。
    """
    if not markdown or table_page_end <= 0:
        return markdown

    group = _last_volume_group(markdown)
    if not group:
        return markdown

    pages_by_num = {getattr(p, "page_num", 0): p for p in getattr(parsed_doc, "pages", [])}
    rows: list[TextScoreTailRow] = []
    section_end = int(getattr(section, "page_end", table_page_end) or table_page_end)
    for page_num in range(table_page_end + 1, section_end + 1):
        page = pages_by_num.get(page_num)
        if page is None:
            continue
        if getattr(page, "tables", None):
            break
        row = _recover_text_score_row(getattr(page, "text", "") or "", page_num, group)
        if row is not None and row.name not in markdown:
            rows.append(row)
    if not rows:
        return markdown

    return _append_rows(markdown, rows)


def _recover_text_score_row(text: str, page_num: int, group: str) -> TextScoreTailRow | None:
    compact = _trim_non_scoring_tail(re.sub(r"\s+", " ", text or "").strip())
    if not compact or not any(term in compact for term in _SCORE_ACTION_TERMS):
        return None

    score = _tail_score(compact)
    if score is None:
        return None

    name = _tail_name(compact, score)
    if not name:
        return None

    return TextScoreTailRow(
        page_num=page_num,
        group=group,
        name=name,
        score=score,
        rule=compact,
    )


def _tail_score(text: str) -> float | None:
    values: list[float] = []
    for match in _SCORE_RE.finditer(text):
        # 排除“0~6分之间”这类区间上界，优先取独立满分列/尾行分值。
        start = match.start(1)
        if start > 0 and text[start - 1] in "~～-/至到":
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if 0 < value <= 200:
            values.append(value)
    return values[-1] if values else None


def _tail_name(text: str, score: float) -> str:
    for match in _ROW_STYLE_RE.finditer(text):
        try:
            candidate_score = float(match.group(2))
        except (TypeError, ValueError):
            continue
        if abs(candidate_score - score) <= 0.0001:
            name = _clean_name(match.group(1))
            if name:
                return name

    joined = _JOINED_NAME_RE.search(text)
    if joined:
        name = _clean_name(joined.group(1))
        if name:
            return name

    candidates = [_clean_name(m.group(1)) for m in _NAME_RE.finditer(text)]
    candidates = [c for c in candidates if c]
    return max(candidates, key=len) if candidates else ""


def _clean_name(raw: str) -> str:
    name = re.sub(r"^[一二三四五六七八九十0-9]+[、.．]?", "", raw or "")
    name = re.sub(r"^(?:根据供应商提供的|供应商提供的|投标人提供的)", "", name)
    name = re.sub(r"[，,。；;：:].*$", "", name)
    name = name.replace(" ", "").strip()
    if name.startswith(("和", "及", "、")):
        return ""
    return name


def _trim_non_scoring_tail(text: str) -> str:
    match = _NON_SCORING_HEADING_RE.search(text)
    if not match:
        return text
    return text[: match.start()].strip()


def _last_volume_group(markdown: str) -> str:
    for line in reversed((markdown or "").splitlines()):
        cells = _markdown_cells(line)
        for cell in cells[:3]:
            compact = cell.replace(" ", "")
            if any(marker in compact for marker in _VOLUME_MARKERS):
                return cell.strip()
    return ""


def _append_rows(markdown: str, rows: list[TextScoreTailRow]) -> str:
    lines = markdown.splitlines()
    if lines:
        lines[0] = _extend_page_range(lines[0], rows[-1].page_num)
    for row in rows:
        lines.append(
            "|  | "
            f"{row.group} | "
            f"{row.name} | "
            f"{row.score:g}分 | "
            f"{row.rule} |"
        )
    return "\n".join(lines)


def _extend_page_range(header: str, page_num: int) -> str:
    match = re.search(r"(第\s*\d+\s*-\s*)(\d+)(\s*页)", header or "")
    if not match:
        return header
    return header[: match.start(2)] + str(max(int(match.group(2)), page_num)) + header[match.end(2):]


def _markdown_cells(line: str) -> list[str]:
    stripped = (line or "").strip()
    if not stripped.startswith("|"):
        return []
    if set(stripped.replace("|", "").replace("-", "").replace(" ", "")) == set():
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


__all__ = ["TextScoreTailRow", "append_trailing_text_score_rows"]
