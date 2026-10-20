"""评分表标题 lot 解析——中性工具模块（无 app 依赖， Task6 O3 单源）。

职责: 定义严格的评分表标题正则，从标题行提取采购包/标段标识（lot）。
 提供两个公开接口：
 resolve_lot_from_first_line(first_line) — 调用方已提取首行时使用。
 resolve_lot_from_content(content) — 统一入口，自动提取首个非空行。
不做: 不调用 LLM/DB、不修改提取结果、不依赖任何 app.* 模块。

此模块是 lot header 解析的**唯一源**：
 - `ledger/declaration_scope.resolve_chunk_lot` 通过它解析 chunk 标题。
 - `scoring_table_parser._extract_lot_from_markdown` 通过它薄委托提取 lot。
 - parser 不得直接 import ledger 包（分层约束）。
"""
from __future__ import annotations

import re

# ── 严格评分表标题正则（O3 单源）──────────────────────────────────────────────
# 格式: [评分表 第N页 ·/• LOT名称]  或  [评分表 第N-M页 ·/• LOT名称]
# 仅认首个非空行，拒绝 [第三章·...] / [附件A·...] 等非评分表标题。
# 支持全角/半角/中点 (·/•/·) 及 第N-M页 数字间可选空格。
_LOT_HEADER_RE = re.compile(
    r"\[评分表\s+第\s*\d+(?:\s*-\s*\d+)?\s*页\s*[·•·]\s*([^\]\n]+)\]"
)


def resolve_lot_from_first_line(first_line: str) -> str | None:
    """从已截取的**首个非空行**中提取 lot 名称（O3 严格格式）。

    匹配: `[评分表 第N页 · LOT名称]` / `[评分表 第N-M页 ·/• LOT名称]`
    拒绝: `[第三章 · 评标办法]` / `[附件A · 报价表]` 等非评分表标题。

    调用方负责传入首个非空行（不传整段文本）。
    """
    m = _LOT_HEADER_RE.search(first_line)
    return m.group(1).strip() if m else None


def resolve_lot_from_content(content: str) -> str | None:
    """从文本中自动提取**首个非空行**并解析 lot 名称（统一入口）。

    消灭 declaration_scope / scoring_table_parser 中的重复"首个非空行"提取逻辑。
    内部调用 resolve_lot_from_first_line，规则一致。
    """
    first_line = next(
        (ln.strip() for ln in (content or "").splitlines() if ln.strip()), ""
    )
    return resolve_lot_from_first_line(first_line)


__all__ = ["resolve_lot_from_content", "resolve_lot_from_first_line"]
