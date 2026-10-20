"""页级文本降噪(P1 页眉页脚剔除 + 中文软换行合并).

职责: 在 ChunkPlanner 规划之前,对 ParsedPage.text 做轻量物理降噪,
      让相同的页眉/页脚噪声不进入 chunk,中文 PDF 提取的硬换行被合并回完整句.
不做: Unicode 归一化(归 text_normalizer.normalize_cjk) / 切块 / 质量判定.

设计原则:
- 仅做"高置信、零误删"的清洗:
  - 重复行需跨多页出现才剔除,避免删掉某页正文短句.
  - 软换行只在"上行无句末标点 且 下行不是标题/条款编号"时合并.
- 修改原地完成(ParsedPage.text 直接替换),不影响调用方对 ParsedPage 的引用.
"""

from __future__ import annotations

import re
from typing import Iterable

from loguru import logger

from app.core.config import settings
from app.core.parser.pdf_parser import ParsedPage

_HEADING_RE = re.compile(
    r"^(第[一二三四五六七八九十百千万0-9]+[章节部分编条]"
    r"|[0-9]+(?:\.[0-9]+){0,4}\s"
    r"|[一二三四五六七八九十]+、"
    r"|（[一二三四五六七八九十0-9]+）"
    r"|\([0-9]+\))"
)

_SENTENCE_END_CHARS = set("。！？；：!?;:、,，.)）】」]》>…")

_REPEAT_LINE_MAX_LEN = 30


def merge_soft_linebreaks(text: str) -> str:
    """合并中文 PDF 物理换行造成的句中断行.

    规则: 当前行非空、末尾非句末标点,且下一行非空、不是标题/条款编号时,
          视为软换行,去掉换行符直接拼接(中文无需空格).
    """
    if not text:
        return text

    lines = text.split("\n")
    if len(lines) <= 1:
        return text

    result: list[str] = []
    for idx, raw_line in enumerate(lines):
        line = raw_line.rstrip()
        if not line:
            if result and result[-1] != "":
                result.append("")
            else:
                result.append("")
            continue

        if not result:
            result.append(line)
            continue

        prev = result[-1]
        if prev == "":
            result.append(line)
            continue

        if _should_merge(prev, line):
            result[-1] = prev + line
        else:
            result.append(line)

    merged = "\n".join(result)
    merged = re.sub(r"\n{3,}", "\n\n", merged)
    return merged


def detect_repeated_lines(
    pages: Iterable[ParsedPage],
    *,
    min_pages: int | None = None,
    max_line_len: int = _REPEAT_LINE_MAX_LEN,
    ratio_threshold: float = 0.6,
) -> set[str]:
    """跨页统计高频短行,识别页眉/页脚.

    判定: 短行(长度 <= max_line_len) 在不同页出现次数 >= max(min_pages, 总页数*ratio).

    Args:
        pages: ParsedPage 序列.
        min_pages: 出现页数下限,默认取 settings.CHUNK_HEADER_FOOTER_MIN_PAGES.
        max_line_len: 视为"短行"的字符上限.
        ratio_threshold: 占总页数的比例门槛,与 min_pages 取较大者.

    Returns:
        被识别为页眉/页脚的行内容集合(已 strip).
    """
    page_list = list(pages)
    total_pages = len(page_list)
    if total_pages < 2:
        return set()

    floor = int(
        min_pages
        if min_pages is not None
        else getattr(settings, "CHUNK_HEADER_FOOTER_MIN_PAGES", 3)
    )
    threshold = max(floor, int(total_pages * ratio_threshold))
    if threshold < 2:
        threshold = 2

    line_pages: dict[str, set[int]] = {}
    for page in page_list:
        text = page.text or ""
        if not text:
            continue
        seen_in_page: set[str] = set()
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line or len(line) > max_line_len:
                continue
            if line in seen_in_page:
                continue
            seen_in_page.add(line)
            line_pages.setdefault(line, set()).add(page.page_num)

    repeated = {line for line, page_nums in line_pages.items() if len(page_nums) >= threshold}
    return repeated


def denoise_pages(
    pages: list[ParsedPage],
    known_repeated_lines: Iterable[str] | None = None,
) -> dict[str, int]:
    """原地降噪 ParsedPage.text: 剔除重复页眉页脚 + 合并软换行.

    known_repeated_lines 可接收 Page Chrome 策略已识别的重复文本,
    作为裁剪后的兜底清理口径。

    Returns:
        dict 摘要 {removed_repeats, merged_pages, repeats_detected}.
    """
    if not pages:
        return {"removed_repeats": 0, "merged_pages": 0, "repeats_detected": 0}

    repeated = detect_repeated_lines(pages)
    if known_repeated_lines:
        repeated = repeated | {
            str(line or "").strip()
            for line in known_repeated_lines
            if str(line or "").strip()
        }
    removed_lines_total = 0
    merged_pages = 0

    for page in pages:
        original = page.text or ""
        if not original:
            continue

        cleaned = original
        if repeated:
            kept_lines: list[str] = []
            removed = 0
            for raw_line in cleaned.split("\n"):
                if raw_line.strip() in repeated:
                    removed += 1
                    continue
                kept_lines.append(raw_line)
            if removed:
                cleaned = "\n".join(kept_lines)
                removed_lines_total += removed

        merged = merge_soft_linebreaks(cleaned)
        if merged != original:
            merged_pages += 1
        page.text = merged

    summary = {
        "removed_repeats": removed_lines_total,
        "merged_pages": merged_pages,
        "repeats_detected": len(repeated),
    }
    if removed_lines_total or merged_pages:
        logger.info(
            "text_cleaner denoise: removed_repeats={removed} merged_pages={merged} "
            "repeats_detected={detected}",
            removed=removed_lines_total,
            merged=merged_pages,
            detected=len(repeated),
        )
    return summary


def _should_merge(prev: str, current: str) -> bool:
    if not prev or not current:
        return False
    if prev[-1] in _SENTENCE_END_CHARS:
        return False
    if _HEADING_RE.match(current):
        return False
    if current.startswith(("•", "·", "◆", "●", "▪", "■", "□", "-", "*")):
        return False
    return True
