"""
职责: 文档质检共享文本形态(目录引导行/清单序号/计量标题)
不做: 不识别标题树、不执行任何检查器判定
"""

from __future__ import annotations

import re

# 目录/索引行: 行尾点线或空白引导的独立页码
INDEX_LINE_RE = re.compile(r"[\s.·…]{1,}\d{1,4}\s*$")
LEADER_PAGE_RE = re.compile(r"[\.·…—\-]{2,}\s*\d{1,4}\s*$")
LIST_MARKER_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s，,。；;：:、]))"
    r"(?:[0-9]{1,2}|[一二三四五六七八九十]{1,3})[)）]"
)
_SUBSET_FONT_RE = re.compile(r"^[A-Za-z0-9]{6,}\+")
# 同一形状的全角/半角互配。（配 ）也配 )；【 配 】也配 ]。
_OPEN_SHAPE = {"（": "paren", "(": "paren", "【": "corner", "[": "corner", "《": "book"}
_CLOSE_SHAPE = {"）": "paren", ")": "paren", "】": "corner", "]": "corner", "》": "book"}
_CN_OR_DIGIT = set("0123456789一二三四五六七八九十")
# 与 model_builder.build_paragraphs 收尾标点同口径, 跨页缝合未完成段
PARA_TERMINALS = ("。", "！", "？", "；", "!", "?", ";")


def is_index_or_leader_line(text: str) -> bool:
    """点线目录或行尾独立页码, 不是正文标题/caption。"""
    stripped = (text or "").strip()
    if not stripped:
        return False
    return bool(INDEX_LINE_RE.search(stripped) or LEADER_PAGE_RE.search(stripped))


def strip_list_markers(text: str) -> str:
    """去掉行首或句读后的 1)/2) 清单序号, 避免把闭括号计入配对。"""
    return LIST_MARKER_RE.sub(" ", text or "")


def _walk_brackets(text: str) -> tuple[list[int], list[int]]:
    """返回未配开括号、多余闭括号的下标。同一形状的全角和半角互配。"""
    leftover: list[int] = []
    extra: list[int] = []
    stack: list[tuple[str, int]] = []
    raw = text or ""
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char in _CN_OR_DIGIT:
            cursor = index
            while cursor < length and raw[cursor] in _CN_OR_DIGIT:
                cursor += 1
            if cursor < length and raw[cursor] in "）)":
                if not stack:
                    index = cursor + 1
                    continue
        if char in _OPEN_SHAPE:
            stack.append((_OPEN_SHAPE[char], index))
        elif char in _CLOSE_SHAPE:
            shape = _CLOSE_SHAPE[char]
            if stack and stack[-1][0] == shape:
                stack.pop()
            else:
                extra.append(index)
        index += 1
    leftover.extend(item[1] for item in stack)
    return leftover, extra


def bracket_residual(text: str) -> tuple[int, int]:
    """配对括号后的剩余开/闭数量。栈空时的「数字+闭括号」视为清单序号, 不计入闭合。"""
    leftover, extra = _walk_brackets(text)
    return len(leftover), len(extra)


def first_unmatched_bracket(text: str) -> int | None:
    """第一个未配括号的下标。配平则返回 None。"""
    leftover, extra = _walk_brackets(text)
    indexes = leftover + extra
    return min(indexes) if indexes else None


def strip_font_subset_prefix(font: str) -> str:
    raw = (font or "").strip()
    return _SUBSET_FONT_RE.sub("", raw)


def stitch_page_paragraphs(visual: dict[int, list[str]]) -> list[tuple[int, str]]:
    """把未收尾段落与次页首段缝合成作者段落。

    成对符号必须在同一文本单元上结算; 页边界不是段落边界。
    返回 (起始页, 段落文本)。
    """
    units: list[tuple[int, str]] = []
    carry: str | None = None
    carry_page: int | None = None
    for page in sorted(visual):
        paras = list(visual.get(page) or [])
        if carry is not None and carry_page is not None:
            if paras:
                units.append((carry_page, carry + paras[0]))
                paras = paras[1:]
            else:
                units.append((carry_page, carry))
            carry = None
            carry_page = None
        if not paras:
            continue
        for para in paras[:-1]:
            units.append((page, para))
        last = paras[-1]
        if last.endswith(PARA_TERMINALS):
            units.append((page, last))
        else:
            carry = last
            carry_page = page
    if carry is not None and carry_page is not None:
        units.append((carry_page, carry))
    return units
