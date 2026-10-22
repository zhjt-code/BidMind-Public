"""多 scope 账本契约——scope 常量与符号重导出（Task 6 结构整改 O5）。

职责: 提供 scope 常量（SINGLE_SCOPE/SINGLE_LOT_CONST/UNASSIGNED_LOT/DEFAULT_SCOPE）
 并重导出 ScopeIdentity / Applicability / ScopedReport（已移至 contracts.py）。
不做: 不定义新数据类型（避免第三套平行 NamedTuple），不调用 LLM/DB。

架构说明（O5 结构整改）:
 ScopeIdentity / Applicability / ScopedReport 已移至 ledger/contracts.py，
 与 LedgerReport 同文件，消除 get_type_hints(LedgerReport) 的前向引用 NameError。
 本模块作为向后兼容的重导出层，调用方继续 `from scoped_contracts import ScopedReport`。
"""
from __future__ import annotations

# ── 重导出（向后兼容） ─────────────────────────────────────────────────────
from app.core.extractor.scoring.ledger.contracts import (  # noqa: F401
    Applicability,
    ScopedReport,
    ScopeIdentity,
)
from app.core.extractor.scoring.scoring_facts import ScoringScopeKey  # noqa: F401

# ── scope 常量（唯一复用 ScoringScopeKey，不引入第三套字面量） ──────────────
SINGLE_SCOPE: str = ScoringScopeKey.SINGLE_SCHEME      # "__single__"（scheme 缺省）
SINGLE_LOT_CONST: str = ScoringScopeKey.SINGLE_LOT     # "__single__"（lot 缺省）
UNASSIGNED_LOT: str = ScoringScopeKey.UNASSIGNED_LOT   # "__unassigned__"

# 默认 scope（单 scope 文档缺省值）
DEFAULT_SCOPE: ScopeIdentity = (
    ScoringScopeKey.SINGLE_SCHEME,
    ScoringScopeKey.SINGLE_LOT,
)

__all__ = [
    "Applicability",
    "DEFAULT_SCOPE",
    "SINGLE_LOT_CONST",
    "SINGLE_SCOPE",
    "ScopeIdentity",
    "ScopedReport",
    "UNASSIGNED_LOT",
]
