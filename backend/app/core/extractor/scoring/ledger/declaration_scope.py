"""结构化总分公式 scope 绑定——chunk lot 解析与公式总分提取（Task 6 修订 O1/O3）。

职责:
 1. 从 chunk payload lot 或 markdown 标题头解析 chunk 所属采购包（lot），
 供 scoped_reconciliation 按 lot 分页过滤（O3 单源: 委托 scoring_lot_header）。
 2. 从页文本中提取结构化总分公式产物（如 FA=...=100分(满分时)），
 绑定到所在 scope，注入 LedgerCapture.total_candidates 前置候选。

不做: 不捕获评分项、不对账、不调用 LLM/DB、不修改提取结果。

设计决策（O1/O3 修订）:
- O3 头部正则: 委托 scoring_lot_header.resolve_lot_from_first_line（单源，
 与 scoring_table_parser 同源，分层约束：declaration_scope 不依赖 parser）。
- O1 公式门禁: 对每个命中点做直接 LHS/邻域判定，不再对整段文本做语境检查。
 允许条件（任一满足）:
 (a) LHS 末端为总分语境词（评标总得分/综合得分/FA 等），
 且 LHS 不以项级前缀开头（其中本项/该项/单项 等）。
 (b) LHS 中含 >=2 个 F×A 加权求和项。
 拒绝示例:
 `综合得分计算：技术=70分(满分)` — LHS(`技术`) 无合法语境。
 `其中本项综合得分=8分(满分)` — LHS(`其中本项综合得分`) 含项级前缀，拒绝。
 `同页前面有总分词…本项=8分(满分时)` — LHS(`本项`) 无合法语境。
"""
from __future__ import annotations

import re

from app.core.extractor.scoring.scoring_lot_header import resolve_lot_from_content

# ── O1: 公式总分门禁 ────────────────────────────────────────────────────────

# 精确括号匹配: (满分时) 或 (满分) ——括号内不允许有数字（排除 (满分8分)/(满分30分)）
_FORMULA_BRACKET_RE = re.compile(
    r"=\s*(\d+(?:\.\d+)?)\s*分\s*[（(]\s*满分\s*(?:时\s*)?[）)]"
)

# 子句分隔符: 用于截取每个命中点的"直接 LHS"
_CLAUSE_SEP_RE = re.compile(r"[，。；：\n\r；]+")

# LHS 末端总分词（含 FA — 综合评标方式首字母缩写）
_LHS_TOTAL_END_RE = re.compile(
    r"(?:评标总得分|综合得分|总得分|最终得分|评标得分|综合评分|评标总分|FA)$"
)

# 项级前缀（拒绝: 即使 LHS 末端含总分词也不得通过）
# 例: `其中本项综合得分=8分(满分)` → LHS 前缀=`其中本项` → 拒绝
_ITEM_LEVEL_PREFIX_RE = re.compile(r"^(?:其中本项|该项|单项|本项|本评分项|各项|每项)")

# F×A 加权求和结构: F1×A1 / Fn×An / F×A 等（全角/半角/ascii 乘号）
_FXA_PAIR_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9]*\s*[×x\*]\s*[A-Za-z][A-Za-z0-9]*"
)


def _get_direct_lhs(text: str, match_start: int) -> str:
    """提取命中点前的直接左上下文（最后一个子句分隔符到 match 开始）。

    O1: 避免将整段文本当作语境——仅取最近子句边界后的内容，
    使 `综合得分计算：技术=70分(满分)` 中 `=70分(满分)` 的 LHS 仅为 `技术`。
    """
    prefix = text[:match_start]
    seps = list(_CLAUSE_SEP_RE.finditer(prefix))
    if seps:
        last_sep_end = seps[-1].end()
        return prefix[last_sep_end:].strip()
    return prefix.strip()


def _lhs_has_valid_context(lhs: str) -> bool:
    """O1: 判断直接 LHS 是否满足公式总分的合法上下文条件。

    优先拒绝项级前缀（即使末端含总分词也不得通过）：
      `其中本项综合得分` — 含 `其中本项` 前缀 → 拒绝。
      `本项评分`        — 含 `本项` 前缀    → 拒绝。

    合法条件（项级前缀排除后，任一满足）:
      (a) LHS 末端为总分语境词（评标总得分/综合得分/FA 等）。
      (b) LHS 含 >=2 个 F×A 加权求和项（说明 LHS 本身就是总分公式链）。
    """
    if _ITEM_LEVEL_PREFIX_RE.match(lhs):
        return False  # 项级前缀优先拒绝
    if _LHS_TOTAL_END_RE.search(lhs):
        return True
    return len(_FXA_PAIR_RE.findall(lhs)) >= 2


def resolve_chunk_lot(content: str, payload_lot: str | None) -> str | None:
    """从 payload lot 或 markdown 标题头解析 chunk 所属采购包标识（O3/I2 单源）。

    优先取 payload.lot；缺失时扫描 content 的首个非空行，
    委托 scoring_lot_header.resolve_lot_from_first_line（严格格式，O3 单源）。
    两者均无法解析时返回 None（= 无 scope chunk）。

    注: scoring_table_parser._extract_lot_from_markdown 薄委托本函数（O3 单源）。
    注: parser 不得 import ledger 包——两者共同依赖 scoring_lot_header（分层约束）。
    """
    if payload_lot:
        return payload_lot
    # 委托 scoring_lot_header.resolve_lot_from_content 统一提取首个非空行（消灭重复逻辑）
    return resolve_lot_from_content(content or "")


def extract_formula_total_candidates(texts: list[str]) -> list[float]:
    """从多段页文本中提取结构化总分公式产物（O1 修订: 每命中点直接 LHS 检查）。

    O1 精确门禁:
    - 括号须精确为 (满分时) 或 (满分)（不含数字），排除 (满分8分)/(满分30分)。
    - 每个命中点独立检查其直接 LHS（最近子句分隔符之后的文本）:
        先排除项级前缀（其中本项/该项/单项/本项等）→ 拒绝。
        再检查 (a) 末端总分词 OR (b) >=2 个 F×A 加权项 → 允许。
      否则拒绝。

    拒绝示例:
      "综合得分计算：技术=70分(满分)，商务=30分(满分)" → []
      "其中本项综合得分=8分(满分)" → []（含项级前缀）
      "汇总如下。本项评分=8分(满分时)" → []

    允许示例:
      "FA=F1×A1+F2×A2=100分(满分时)" → [100.0]
      "评标总得分=100分（满分）"       → [100.0]
    """
    seen: set[float] = set()
    out: list[float] = []
    for text in texts:
        text_str = str(text or "")
        for m in _FORMULA_BRACKET_RE.finditer(text_str):
            lhs = _get_direct_lhs(text_str, m.start())
            if not _lhs_has_valid_context(lhs):
                continue
            try:
                v = round(float(m.group(1)), 4)
                if v not in seen:
                    seen.add(v)
                    out.append(v)
            except ValueError:
                continue
    return sorted(out)


__all__ = ["resolve_chunk_lot", "extract_formula_total_candidates"]
