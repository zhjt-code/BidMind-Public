"""合计行判定策略（精确化）。

职责: 提供精确的"合计/结果汇总行"判定——仅短结构单元格命中，长规则正文中的同名词不命中。
不做: 不判断整表类型、不解析分值列、不访问 DB/LLM、不依赖 score_column_semantics。

设计原因（根因）:
 原 is_total_row 用 row_text 拼接整行后全串匹配 TOTAL_ROW_TERMS，导致
 "前10项有效业绩的水表数量合计情况" 中的"合计"子串也会命中，使真实评分行
 被 _column_has_short_score 排除，分值列双向验证失败，评分行静默丢失。

判据: 逐单元格检查——去空格后长度 ≤ _MAX_TOTAL_CELL_LEN 且含合计词才命中；
 超过阈值视为规则正文，不触发信号。与表头单元格上限同口径（_HEADER_CELL_MAX_LEN=10）。
"""

from __future__ import annotations

import re

# 与 score_column_semantics.TOTAL_ROW_TERMS 同值；此模块自包含，不引入循环依赖。
TOTAL_ROW_TERMS: tuple[str, ...] = (
    "合计",
    "得分小计",
    "平均分",
    "加权得分",
    "最终得分",
)

# 合计单元格最大有效字符数；超出视为规则正文，与 _HEADER_CELL_MAX_LEN 同口径。
_MAX_TOTAL_CELL_LEN = 10

_WS_RE = re.compile(r"\s+")


def _norm(text: object) -> str:
    """去空格归一化（与 normalize_cell 等价，本模块自包含）。"""
    return _WS_RE.sub("", str(text or ""))


def is_total_row_strict(row: list[str]) -> bool:
    """精确判定合计/汇总行——仅短结构单元格以合计词结尾时命中（C1 精确化）。

 规则: 遍历行内每个单元格；归一化后长度 ≤ _MAX_TOTAL_CELL_LEN 且以 TOTAL_ROW_TERMS
 中任意词结尾（endswith），则判定为合计行。
 - 使用 endswith 而非 contains，拦截"数量合计情况"/"某得分小计情况"等短反例——
 这类格中合计词不在末尾，属于描述性后缀，不是汇总行语义。
 - 同时保持"合计"/"标准分合计"/"技术评分合计"/"得分小计"等以词结尾的形态。
 - 超过 _MAX_TOTAL_CELL_LEN 的格视为规则正文，不触发（原有长度守护）。
 """
    for cell in row:
        compact = _norm(cell)
        if not compact or len(compact) > _MAX_TOTAL_CELL_LEN:
            continue
        if any(compact.endswith(term) for term in TOTAL_ROW_TERMS):
            return True
    return False


__all__ = [
    "TOTAL_ROW_TERMS",
    "is_total_row_strict",
]
