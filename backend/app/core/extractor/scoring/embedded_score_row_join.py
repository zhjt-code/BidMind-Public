"""嵌入式分值跨行粘合。

职责: 在扫描`对象名（N分）`前, 修复PDF拆行导致的`（2`/`分）`断裂。
不做: 不解析分值、不创建评分项、不跨非相邻行重排。
"""

from __future__ import annotations

import re

_OPEN_SCORE_RE = re.compile(r"[（(]\s*\d+(?:\.\d+)?\s*$")
_CLOSE_SCORE_RE = re.compile(r"^\s*分\s*[）)]")
_FULL_SCORE_RE = re.compile(r"^\s*[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def join_split_embedded_score_rows(rows: list[list[str]]) -> list[list[str]]:
    """合并相邻行中的断裂括号分值。

    只处理上一行某格以`（N`结尾、下一行同列或任一短格以`分）`开头的强形态。
    合并后下一行被清空的格不再参与候选扫描, 其余规则文本仍留在原行。
    """
    out = [list(row) for row in rows]
    for idx in range(len(out) - 1):
        current = out[idx]
        nxt = out[idx + 1]
        for col, cell in enumerate(current):
            if not _OPEN_SCORE_RE.search(str(cell or "").replace(" ", "")):
                continue
            close_col = _find_closing_col(nxt, preferred=col)
            if close_col is None:
                continue
            current[col] = f"{str(cell or '').rstrip()}{str(nxt[close_col] or '').lstrip()}"
            nxt[close_col] = ""
            break
        for col, cell in enumerate(nxt):
            if not _FULL_SCORE_RE.match(str(cell or "").replace(" ", "")):
                continue
            if col >= len(current):
                continue
            prev = str(current[col] or "").strip()
            if not prev or not _CJK_RE.search(prev) or "分" in prev:
                continue
            current[col] = f"{prev}{str(cell or '').strip()}"
            nxt[col] = ""
            break
    return out


def _find_closing_col(row: list[str], *, preferred: int) -> int | None:
    if preferred < len(row) and _CLOSE_SCORE_RE.match(str(row[preferred] or "").replace(" ", "")):
        return preferred
    for idx, cell in enumerate(row):
        text = str(cell or "").replace(" ", "")
        if len(text) <= 12 and _CLOSE_SCORE_RE.match(text):
            return idx
    return None


__all__ = ["join_split_embedded_score_rows"]
