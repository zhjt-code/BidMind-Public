"""评分声明账本契约（换轴重构 P1，frozen dataclass）。

职责: 定义账本各阶段的不可变数据结构——声明对、捕获结果、组级对账行、
 四级基准层级、账本报告，以及 Task6 新增的 ScopedReport。
 语义（评估未完成与业务未采纳同走 REVIEW_REQUIRED）在状态枚举层固化。
不做: 不实现捕获/对账算法、不做枚举卷映射、不调用 LLM/DB、不落库。

 Task6 结构整改（O5）:
 ScopeIdentity / Applicability / ScopedReport 统一放置于本模块（紧靠 LedgerReport
 定义处），避免前向字符串引用导致 get_type_hints 报 NameError。
 scoped_contracts.py 从本模块重导出上述符号，保持向后兼容。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


@dataclass(frozen=True)
class LeafIntegrityEvidence:
    """叶子完整性正向证据（步骤2）。

 verified: 是否通过叶子一一匹配验证；
 basis: '' | 'single_100'（scope 内 100 分通过）|
 'per_scope_100'（仅多 scope 顶层聚合使用；成员仍为 single_100）；
 inventory_count: 网格叶子清单数量；
 matched_count: 与账本条目精确匹配的叶子数量；
 total: 单 scope 为网格叶子总分；多 scope 顶层为各 scope 叶子总分之和。
 """

    verified: bool = False
    basis: Literal["", "single_100", "per_scope_100"] = ""
    inventory_count: int = 0
    matched_count: int = 0
    total: float = 0.0


class BasisLevel(str, Enum):
    """四级降级链的基准层级（探针 v2 实测口径固化）。

    L1_EXPLICIT_GROUP: 显式组级声明基准（高置信，可支撑组级缺口定位）；
    L2_DERIVED_GROUP: 组标签推导基准（低置信，仅供缺口提示，不作通过依据）；
    L3_DOC_TOTAL: 仅文档级显式总分基准；
    L4_NONE: 无任何基准（含默认百分制假设），恒 REVIEW_REQUIRED。
    """

    L1_EXPLICIT_GROUP = "l1_explicit_group"
    L2_DERIVED_GROUP = "l2_derived_group"
    L3_DOC_TOTAL = "l3_doc_total"
    L4_NONE = "l4_none"


class LedgerStatus(str, Enum):
    """账本报告三态。

 语义: "评估未完成"（无基准/信号缺失）与"业务未采纳"（有基准但
 不闭合/多方案/冲突）都必须落 REVIEW_REQUIRED，禁止按字段是否存在分叉。
 RECONCILED 仅当高置信基准存在且对账闭合且无多方案信号。
 """

    RECONCILED = "reconciled"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True)
class DeclarationPair:
    """单条原文组声明对（永不映射枚举卷）。

    label: 原文标签（如 "商务部分"）；norm_label: 归一化标签（匹配键）；
    score: 声明分值；weight: 折算权重（0-1，可空）；
    source: 捕获来源（anchor_gated/flat/inline/each_part/weighted_def/
    group_paren/group_bare）；page: 来源页码（可空）。
    """

    label: str
    norm_label: str
    score: float
    weight: float | None = None
    source: str = ""
    page: int | None = None


@dataclass(frozen=True)
class LedgerCapture:
    """声明捕获结果（R1 产物）。

 pairs: 去重后的组声明对；total_candidates: 显式总分候选（升序去重）；
 conflicts: 同标签冲突声明的 fail-visible 记录（同域冲突拒用）；
 scheme_evidence: 文档级"多套备选评分方案"的独立证据（模板编码锚点等，
 ：数值形态不构成方案身份，缺该证据一律不得断言多方案）；
 parallel_scope_norms: 由表尾合计+标题+闭合验证得到的并行评分卷归一键；
 diagnostics: 捕获过程诊断（被负向规则排除的候选等）。
 """

    pairs: tuple[DeclarationPair, ...] = ()
    total_candidates: tuple[float, ...] = ()
    conflicts: tuple[str, ...] = ()
    scheme_evidence: bool = False
    parallel_scope_norms: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class LedgerItemEntry:
    """账本对账的提取项条目（：作用域维度必须随条目进入账本）。

 name: 评分项名；score: 分值；volume_label: 原文组标签（可空，空即未归组）；
 scheme: 评分方案标识（互斥备选模板，唯一可支撑"多方案"断言的身份维度）；
 lot: 采购包/标段标识（并行采购范围，与方案维度无关）。

 历史上适配层只传 (name, score, volume_label) 三元组，账本因此无法区分
 "同一方案下的并行评分卷"与"多套并存方案"，把前者误判为后者。
 """

    name: str
    score: float
    volume_label: str = ""
    scheme: str | None = None
    lot: str | None = None


def coerce_ledger_item_entries(
    raw_entries: "Sequence[LedgerItemEntry | tuple[str, float, str]]",
) -> tuple[LedgerItemEntry, ...]:
    """把条目入参归一为 LedgerItemEntry 元组（兼容既有三元组调用方）。

    三元组不携带作用域，归一后 scheme/lot 为 None——即"无方案身份证据"，
    对账器据此拒绝做方案类断言，而不是退回按分值猜测。
    """
    out: list[LedgerItemEntry] = []
    for raw in raw_entries:
        if isinstance(raw, LedgerItemEntry):
            out.append(raw)
            continue
        name, score, label = raw
        out.append(
            LedgerItemEntry(
                name=str(name or ""),
                score=float(score or 0.0),
                volume_label=str(label or ""),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class GroupReconciliation:
    """单组对账行: 声明值 vs 提取组内和。gap = extracted_sum - declared。"""

    label: str
    norm_label: str
    declared: float
    extracted_sum: float
    gap: float
    declared_source: str = ""


# ── Task6: ScopeIdentity / Applicability / ScopedReport ────────────
# 放置于 LedgerReport 之前，使 LedgerReport.scope_reports 字段可直接引用
# ScopedReport 类型（非字符串前向引用），从而 get_type_hints(LedgerReport) 正常。

# ScopeIdentity: (scheme, lot) 元组，与 scoring_scope_contracts 保持语义一致。
# 此处定义为 type alias，不引入外部模块依赖，供 contracts 内部使用。
ScopeIdentity = tuple[str, str]

# applicability 类型（I4/O5: Literal，禁止任意字符串）
Applicability = Literal["applicable", "unresolved"]

# lot 内部哨兵值（单值常量保持私有，避免导入 ScoringScopeKey 引入外部依赖）
_SINGLE_LOT_VALUE = "__single__"
_UNASSIGNED_LOT_VALUE = "__unassigned__"

# 公共哨兵集合：任何 lot 属于此集合时均不得暴露给显示层，统一回退显示 scheme。
# 供 ScopedReport._default_scope_id 及 scoped_aggregation 共同引用（单源）。
SCOPE_SENTINEL_LOTS: frozenset[str] = frozenset({_SINGLE_LOT_VALUE, _UNASSIGNED_LOT_VALUE})


@dataclass(frozen=True)
class ScopedReport:
    """单 scope 子报告（LedgerReport.scope_reports 的成员类型， Task6）。

 职责: 承载单个 (scheme, lot) scope 的对账结果与可审计字段。
 不做: 不调用 LLM/DB、不修改 LedgerCapture/LedgerReport、不做全局聚合。

 scope: (scheme, lot) 元组，与 ScopeIdentity = tuple[str, str] 一致。
 scope_display_id: 由 coordinator 按唯一性上下文赋值（O3）:
 - 同 lot 多 scheme → "scheme·lot" （保唯一）
 - 单 scheme per lot → lot （最常见）
 - lot==__single__ → scheme （无 lot scope）
 若未赋值则退化到 _default_scope_id 逻辑。
 applicability:
 'applicable' — 正常对账，有权威基准，unlabeled/insufficiency 在容差内。
 'unresolved' — 无权威基准 / __unassigned__ / scheme 声明歧义 /
 unlabeled_sum>容差 / insufficiency / basis_low_confidence。
 basis_low_confidence / diagnostics / unlabeled_sum /
 insufficiency_flags / multi_scheme_signal: 可审计字段（C4 顶层守恒汇总）。
 expected_by_volume: (norm_label, declared) 序列，供卷级展示。
 """

    scope: ScopeIdentity                              # (scheme, lot) tuple
    status: LedgerStatus
    basis: BasisLevel
    expected_total: float | None
    extracted_total: float
    total_gap: float | None
    expected_by_volume: tuple[tuple[str, float], ...] = ()
    applicability: Applicability = "applicable"
    basis_low_confidence: bool = False
    diagnostics: tuple[str, ...] = ()
    unlabeled_sum: float = 0.0
    insufficiency_flags: tuple[str, ...] = ()
    multi_scheme_signal: str = ""
    # O3: coordinator 按唯一性上下文设置的显示标识（同 lot 多 scheme → "scheme·lot"）
    scope_display_id: str = ""
    # Step2: 叶子完整性正向证据（默认未验证）
    leaf_integrity: LeafIntegrityEvidence = field(default_factory=LeafIntegrityEvidence)

    def _default_scope_id(self) -> str:
        """退化逻辑: lot 为可见业务值时取 lot，否则取 scheme（哨兵不暴露）。

        __single__ 和 __unassigned__ 均为内部哨兵，不得泄漏到显示层——
        两者均回退到 scheme 作为显示标识（与 build_scope_display_ids 保持一致）。
        """
        lot = self.scope[1]
        if lot in SCOPE_SENTINEL_LOTS:
            return self.scope[0]  # 哨兵 lot → 显示 scheme
        return lot

    @property
    def scope_id(self) -> str:
        """O3 唯一性 scope 标识（由 coordinator 赋 scope_display_id 后使用）。"""
        return self.scope_display_id or self._default_scope_id()


@dataclass(frozen=True)
class LedgerReport:
    """账本对账报告（对账器唯一输出，职责=比对与报警，不搜索不指派）。

 status: 三态（见 LedgerStatus）；basis_level: 基准层级；
 basis_low_confidence: L2/默认100 兜底时恒 True；
 expected_total: 采用的对账基准总分（可空=无基准）；
 extracted_total: 提取合计（**原始满分轴**的事实值，与 unlabeled_sum 同轴）；
 extracted_total_folded: 折算制下提取侧折算到基准同轴后的合计（非折算制或
 覆盖不完整时为 None，：两侧不同轴时禁止相减）；
 total_gap: 与 expected_total 同轴的差额（折算制取 extracted_total_folded）；
 group_rows: 组级对账明细（仅 L1/L2 可用）；
 unlabeled_sum: 未归组提取分值合计（fail-visible， 教训）；
 multi_scheme_signal: 多方案信号说明（非空即强制 REVIEW_REQUIRED）；
 duplicate_exclusions: 副本行排除记录；
 insufficiency_flags: 证据充分性拒收记录；
 inferred_group_*: 适配层在对账前完成的唯一余项守恒归集说明（不改变落库事实）；
 diagnostics: 其余诊断。
 scope_reports: 多 scope 子报告（Task6 加法扩展，空=单 scope 旧行为）；
 global_comparison_allowed: 多 scope 时 False，禁止跨 scope 全局比较。
 """

    status: LedgerStatus
    basis_level: BasisLevel
    basis_low_confidence: bool
    expected_total: float | None
    extracted_total: float
    total_gap: float | None
    group_rows: tuple[GroupReconciliation, ...] = ()
    unlabeled_sum: float = 0.0
    extracted_total_folded: float | None = None
    multi_scheme_signal: str = ""
    duplicate_exclusions: tuple[str, ...] = ()
    insufficiency_flags: tuple[str, ...] = ()
    inferred_group_label: str = ""
    inferred_group_sum: float = 0.0
    inferred_group_count: int = 0
    diagnostics: tuple[str, ...] = ()
    # Task6: ScopedReport 已定义在上方，可直接引用（非字符串前向引用）
    scope_reports: tuple[ScopedReport, ...] = ()
    global_comparison_allowed: bool = True
    # Step2: 叶子完整性正向证据（默认未验证；多 scope 时为聚合结果）
    leaf_integrity: LeafIntegrityEvidence = field(default_factory=LeafIntegrityEvidence)

    def __post_init__(self) -> None:
        if self.status == LedgerStatus.RECONCILED:
            if self.basis_low_confidence:
                raise ValueError("RECONCILED 不允许低置信基准")
            if self.basis_level not in (BasisLevel.L1_EXPLICIT_GROUP, BasisLevel.L3_DOC_TOTAL):
                raise ValueError("RECONCILED 仅允许 L1/L3 高置信基准")
            if self.multi_scheme_signal:
                raise ValueError("RECONCILED 不允许存在多方案信号")


DEFAULT_TOTAL_ASSUMPTION = 100.0
LEDGER_TOLERANCE = 1.0
SCORE_MATCH_TOLERANCE = 0.01

__all__ = [
    "Applicability",
    "BasisLevel",
    "LeafIntegrityEvidence",
    "DEFAULT_TOTAL_ASSUMPTION",
    "DeclarationPair",
    "GroupReconciliation",
    "LEDGER_TOLERANCE",
    "LedgerCapture",
    "LedgerItemEntry",
    "LedgerReport",
    "LedgerStatus",
    "SCOPE_SENTINEL_LOTS",
    "SCORE_MATCH_TOLERANCE",
    "ScopeIdentity",
    "ScopedReport",
    "coerce_ledger_item_entries",
]
