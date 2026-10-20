"""
职责: 无点线目录的宽松解析(仅文档质检消费)——覆盖两种形态:
      同行"标题␣页码"与两行式"标题行+纯页码行"
不做: 不修改共享 TocParser(提取链路共用, 其点线契约不动)、不做坐标可靠性判定
      (印刷页->物理页换算复用 resolve_toc_page_coordinates)
"""

from __future__ import annotations

import re

from app.core.parser.toc_parser import TocEntry

# 同行形态: 标题 + 空白 + 行尾页码("1 项目理解与总体目标 6")
_INLINE_RE = re.compile(r"^(?P<title>\S.{1,38}?)\s+(?P<page>\d{1,4})$")
# 两行形态的页码行: 整行纯数字
_PAGE_LINE_RE = re.compile(r"^\d{1,4}$")
# 标题行否定特征: 以句读结尾的整句不是目录条目
_SENTENCE_END = ("。", "；", "！", "？", ";", "!", "?", "，", ",")
# 主目录终止标志: 图表索引等附属目录开始
_STOP_TOKENS = ("索引", "图目录", "表目录")

_MIN_PAIRS = 3
_MAX_TITLE_LEN = 40


def _is_title_text(text: str) -> bool:
    text = text.strip()
    if not (2 <= len(text) <= _MAX_TITLE_LEN):
        return False
    if text.endswith(_SENTENCE_END):
        return False
    if _PAGE_LINE_RE.match(text):
        return False
    return True


def _find_toc_start(body_texts: dict[int, str], max_pages: int) -> tuple[int, int] | None:
    """定位"目录"标题行 -> (页号, 行号); 未找到返回 None。"""
    for page_num in sorted(body_texts):
        if page_num > max_pages:
            break
        lines = (body_texts[page_num] or "").split("\n")
        for index, line in enumerate(lines):
            compact = re.sub(r"\s+", "", line)
            if compact in ("目录", "目錄"):
                return page_num, index
    return None


def extract_loose_toc_entries(
    body_texts: dict[int, str],
    max_pages: int = 6,
) -> tuple[list[TocEntry], set[int]]:
    """解析无点线目录(同行式与两行式共用一条链路)。

    Returns:
        (条目列表, 目录页页码集合); 配对不足 _MIN_PAIRS 时返回空
        (调用方保持 TocParser 原结果, 不降低既有行为)。
    """
    start = _find_toc_start(body_texts, max_pages)
    if start is None:
        return [], set()

    start_page, start_line = start
    entries: list[TocEntry] = []
    toc_pages: set[int] = set()
    seen: set[tuple[str, int]] = set()
    stopped = False

    def _add(title: str, printed_page: int, page_num: int) -> None:
        key = (title, printed_page)
        if 1 <= printed_page <= 999 and key not in seen:
            seen.add(key)
            entries.append(TocEntry(title=title, page_num=printed_page))
            toc_pages.add(page_num)

    for page_num in sorted(body_texts):
        if page_num < start_page or page_num > max_pages or stopped:
            continue
        lines = [ln.strip() for ln in (body_texts[page_num] or "").split("\n")]
        index = start_line + 1 if page_num == start_page else 0
        while index < len(lines):
            line = lines[index]
            if any(token in line for token in _STOP_TOKENS):
                stopped = True
                break
            # 形态1: 同行"标题␣页码"
            inline = _INLINE_RE.match(line)
            if inline and _is_title_text(inline.group("title")):
                _add(inline.group("title").strip(), int(inline.group("page")), page_num)
                index += 1
                continue
            # 形态2: 标题行 + 下一行纯页码
            if (
                index + 1 < len(lines)
                and _is_title_text(line)
                and _PAGE_LINE_RE.match(lines[index + 1])
            ):
                _add(line, int(lines[index + 1]), page_num)
                index += 2
                continue
            index += 1

    if len(entries) < _MIN_PAIRS:
        return [], set()
    return entries, toc_pages
