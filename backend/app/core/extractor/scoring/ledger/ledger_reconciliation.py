"""评分声明账本对账器（换轴重构 P1，探针 v2 四级基准分级产品化）。

职责: 以显式组声明与组标签推导双源为基准, 对提取项分值做组级/文档级比对
 与报警——多方案信号检测、四级基准分级、组级缺口定位、账本状态判定;
 吸收 独立总分门禁语义(期望约100、容差±1、超百分制疑多方案)。
不做: 不搜索子集、不指派项归属、不凑分(kernel 子集和凑分经圆桌裁决禁止
 迁入)、不做枚举卷映射、不调用 LLM/DB、不修改捕获结果与提取结果。

语义约束:
-: "评估未完成"(无基准/信号缺失)与"业务未采纳"(有基准但不闭合/
 多方案/冲突)同走 REVIEW_REQUIRED, 禁止按字段是否存在分叉;
- 迁移: 调用方传入的 item_entries 必须只含基础评分项
 (base_score/price_formula 口径), bonus/penalty 调整项不得计入基础满分。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.extractor.scoring.ledger.contracts import (
    DEFAULT_TOTAL_ASSUMPTION,
    LEDGER_TOLERANCE,
    BasisLevel,
    DeclarationPair,
    GroupReconciliation,
    LedgerCapture,
    LedgerItemEntry,
    LedgerReport,
    LedgerStatus,
    coerce_ledger_item_entries,
)
from app.core.extractor.scoring.ledger.label_normalization import normalize_group_label

# 多方案信号阈值: 超过"默认百分制+容差"即疑似多套账本并存(语义)。
_MULTI_SCHEME_THRESHOLD = DEFAULT_TOTAL_ASSUMPTION + LEDGER_TOLERANCE


@dataclass(frozen=True)
class _BasisResolution:
    """基准分级解析结果(模块内部中转): 层级/置信/基准总分/折算轴/诊断。"""

    level: BasisLevel
    low_confidence: bool
    expected_total: float | None
    folded_axis: bool
    diagnostics: tuple[str, ...] = ()


def _pair_effective_score(pair: DeclarationPair) -> float:
    """组声明对的有效分值: 带权重时按折算值(score*weight), 否则取原始分值。"""
    if pair.weight is not None:
        return pair.score * pair.weight
    return pair.score


@dataclass(frozen=True)
class _ExtractionAggregate:
    """提取侧聚合(模块内部中转): 组标签轴、方案轴、未归组与总计。"""

    derived: dict[str, float] = field(default_factory=dict)
    by_scheme: dict[str, float] = field(default_factory=dict)
    unlabeled_sum: float = 0.0
    extracted_total: float = 0.0
    diagnostics: tuple[str, ...] = ()


def _aggregate_extraction(entries: tuple[LedgerItemEntry, ...]) -> _ExtractionAggregate:
    """步骤a: 按归一化组标签与评分方案双轴聚合提取项分值。

 组标签轴(derived)供组级对账; 方案轴(by_scheme)供方案身份判定——两者是不同
 维度: 同一方案下可有多个并行评分卷, 同一评分卷也可跨方案重复出现。
 空标签项入 unlabeled_sum 并输出 fail-visible 诊断(: 未归组禁止静默丢弃)。
 """
    derived: dict[str, float] = {}
    by_scheme: dict[str, float] = {}
    unlabeled_sum = 0.0
    extracted_total = 0.0
    unlabeled_names: list[str] = []
    for entry in entries:
        value = float(entry.score or 0.0)
        extracted_total += value
        if entry.scheme:
            by_scheme[entry.scheme] = round(by_scheme.get(entry.scheme, 0.0) + value, 4)
        norm = normalize_group_label(entry.volume_label)
        if norm:
            derived[norm] = derived.get(norm, 0.0) + value
        else:
            unlabeled_sum += value
            unlabeled_names.append(str(entry.name))
    diagnostics: tuple[str, ...] = ()
    if unlabeled_names:
        preview = ", ".join(unlabeled_names[:5])
        diagnostics = (
            f"未归组提取项{len(unlabeled_names)}条(合计{unlabeled_sum:g}分): {preview}",
        )
    return _ExtractionAggregate(
        derived=derived,
        by_scheme=by_scheme,
        unlabeled_sum=unlabeled_sum,
        extracted_total=extracted_total,
        diagnostics=diagnostics,
    )


def _detect_multi_scheme_signal(
    capture: LedgerCapture, aggregate: _ExtractionAggregate
) -> str:
    """步骤b: 多方案信号检测(任一命中即非空, 强制 REVIEW_REQUIRED)。

 收口: "多个≈100 的分组"是并行评分卷/多标段的常态形态, 数值闭合只能
 证明分值自洽, 不能证明业务身份。故身份类信号①③一律要求 `scheme_evidence`
 (文档级模板编码锚点等独立证据)背书; ③ 另需条目确实跨 >=2 个 scheme。
 信号② 不是身份断言而是量纲越界(总分超百分制), 与方案证据无关, 保持无条件。
 """
    signals: list[str] = []

    if capture.scheme_evidence:
        effective = [_pair_effective_score(pair) for pair in capture.pairs]
        near_pairs = sum(
            1 for v in effective if abs(v - DEFAULT_TOTAL_ASSUMPTION) <= LEDGER_TOLERANCE
        )
        pair_total = sum(effective)
        if near_pairs >= 2 and pair_total > _MULTI_SCHEME_THRESHOLD:
            signals.append(
                f"文档含多套评分模板证据, 且显式组对中{near_pairs}个有效分值"
                f"≈{DEFAULT_TOTAL_ASSUMPTION:g}分"
            )

    over_candidates = sorted(
        {c for c in capture.total_candidates if c > _MULTI_SCHEME_THRESHOLD}
    )
    if over_candidates:
        listed = ", ".join(f"{c:g}" for c in over_candidates)
        signals.append(
            f"显式总分候选存在超百分制值[{listed}]"
            f"(>{_MULTI_SCHEME_THRESHOLD:g}), 需确认是否多套方案合并计分"
        )

    scheme_totals = aggregate.by_scheme
    if capture.scheme_evidence and len(scheme_totals) >= 2:
        near_schemes = sum(
            1
            for v in scheme_totals.values()
            if abs(v - DEFAULT_TOTAL_ASSUMPTION) <= LEDGER_TOLERANCE
        )
        if near_schemes >= 2:
            signals.append(
                f"提取项分属{len(scheme_totals)}套评分方案, 其中{near_schemes}套"
                f"各自合计≈{DEFAULT_TOTAL_ASSUMPTION:g}分"
            )
    return "; ".join(signals)


def _resolve_doc_total_basis(
    candidates: tuple[float, ...], extracted_total: float
) -> tuple[float, bool, tuple[str, ...]]:
    """L3 基准解析: 返回 (展示基准, 低置信标记, 诊断)。

    换轴红线(P4验证集实测修正): 多个互斥总分候选时**禁止选边**——"取与提取合计
    最近者"等于用提取结果反推基准(闭合作凑分目标的变体, 圆桌裁决禁止)。
    多候选=声明歧义, 恒低置信(REVIEW_REQUIRED); 展示基准仅取最近者供缺口提示。
    """
    unique = sorted(set(candidates))
    if len(unique) == 1:
        return unique[0], False, ()
    display = min(unique, key=lambda c: abs(c - extracted_total))
    listed = ", ".join(f"{c:g}" for c in unique)
    return display, True, (
        f"存在多个互斥总分候选[{listed}], 基准歧义恒低置信(展示基准{display:g}仅供缺口提示)",
    )


def _classify_basis(
    capture: LedgerCapture, derived: dict[str, float], extracted_total: float
) -> _BasisResolution:
    """步骤c/e: 四级基准分级(探针 v2 classify_basis_level 产品化)并定基准总分。

    L1=显式组对>=2且Σ(或折算Σ)≈显式候选(高置信)或默认100(低置信);
    L2=推导组>=2且Σ推导≈候选或默认100(恒低置信, expected 取匹配的候选);
    L3=仅显式总分候选(高置信); L4=无任何基准(恒低置信, expected=None)。
    """
    if len(capture.pairs) >= 2:
        raw_sum = sum(pair.score for pair in capture.pairs)
        folded_sum = sum(_pair_effective_score(pair) for pair in capture.pairs)
        axes = ((raw_sum, False), (folded_sum, True))
        for total, folded in axes:
            if any(abs(total - c) <= LEDGER_TOLERANCE for c in capture.total_candidates):
                return _BasisResolution(
                    BasisLevel.L1_EXPLICIT_GROUP, False, total, folded
                )
        for total, folded in axes:
            if abs(total - DEFAULT_TOTAL_ASSUMPTION) <= LEDGER_TOLERANCE:
                return _BasisResolution(
                    BasisLevel.L1_EXPLICIT_GROUP, True, total, folded,
                    ("L1基准依赖默认100假设(无显式总分候选佐证), 恒低置信",),
                )

    if len(derived) >= 2:
        derived_total = sum(derived.values())
        for candidate in (*capture.total_candidates, DEFAULT_TOTAL_ASSUMPTION):
            if abs(derived_total - candidate) <= LEDGER_TOLERANCE:
                return _BasisResolution(
                    BasisLevel.L2_DERIVED_GROUP, True, candidate, False,
                    ("L2推导组基准恒低置信, 仅供缺口提示不作通过依据",),
                )
        if (
            not capture.total_candidates
            and len(capture.parallel_scope_norms) >= 2
            and set(capture.parallel_scope_norms).issubset(derived)
            and all(
                abs(score - DEFAULT_TOTAL_ASSUMPTION) <= LEDGER_TOLERANCE
                for score in derived.values()
            )
        ):
            # 残留: 单一办法下可有多个并行百分制卷(100/100/100)，
            # 既不应横向套默认100报“多200”，也无原文总分可作高置信依据。
            # 只在每个派生组都各自≈100时按原始满分轴归集为L2；100/100/50拒绝。
            return _BasisResolution(
                BasisLevel.L2_DERIVED_GROUP,
                True,
                derived_total,
                False,
                ("多个并行百分制评分卷按原始满分轴归集，未经原文总分交叉核对",),
            )

    if capture.total_candidates:
        expected, ambiguous, diags = _resolve_doc_total_basis(
            capture.total_candidates, extracted_total
        )
        return _BasisResolution(BasisLevel.L3_DOC_TOTAL, ambiguous, expected, False, diags)

    return _BasisResolution(
        BasisLevel.L4_NONE, True, None, False,
        ("无任何账本基准(L4), 恒REVIEW_REQUIRED",),
    )


@dataclass(frozen=True)
class _AxisResolution:
    """折算轴解析(模块内部中转): 提取侧所处的轴与折算后的提取合计。"""

    declared_folded: bool = False
    extracted_folded: float | None = None
    diagnostics: tuple[str, ...] = ()


def _resolve_folded_axis(
    capture: LedgerCapture,
    aggregate: _ExtractionAggregate,
    basis: _BasisResolution,
) -> _AxisResolution:
    """步骤c2: 折算制下把两侧对齐到同一轴（根治）。

 轴判定是**文档级单一决策**, 依据聚合证据而非逐组挑选:
 - 提取合计≈折算声明和 且 ≠原始声明和 -> 提取侧已折算, 组行按折算值比对;
 - 否则按常见形态视为原始轴, 组行原始比对, 文档级把提取侧逐组乘权重后折算。
 折算要求覆盖完整(无未归组、每个提取组都有权重), 否则返回 None——算不出的
 差额宁可留空并降级, 也不把跨轴相减的结果当缺口报给用户。
 """
    if not basis.folded_axis:
        return _AxisResolution()
    weights = {
        pair.norm_label: pair.weight
        for pair in capture.pairs
        if pair.weight is not None
    }
    raw_declared = sum(pair.score for pair in capture.pairs)
    folded_declared = sum(_pair_effective_score(pair) for pair in capture.pairs)
    extracted = aggregate.extracted_total
    matches_folded = abs(extracted - folded_declared) <= LEDGER_TOLERANCE
    matches_raw = abs(extracted - raw_declared) <= LEDGER_TOLERANCE
    # 缺卷时 raw 合计可碰巧等于折算声明和(例: 仅价格100 ≈ 40+60),
    # 若仍早退为 declared_folded, 组行会用折算声明对原始提取 → 技术-40;价格+40 假缺口。
    weighted_norms = {norm for norm, weight in weights.items() if weight is not None}
    missing_declared = sorted(norm for norm in weighted_norms if norm not in aggregate.derived)
    coverage_complete = (
        aggregate.unlabeled_sum <= LEDGER_TOLERANCE and not missing_declared
    )
    if matches_folded and not matches_raw and coverage_complete:
        return _AxisResolution(declared_folded=True, extracted_folded=round(extracted, 4))

    uncovered = sorted(norm for norm in aggregate.derived if norm not in weights)
    if aggregate.unlabeled_sum > LEDGER_TOLERANCE or uncovered:
        detail = ", ".join(uncovered) if uncovered else f"未归组{aggregate.unlabeled_sum:g}分"
        return _AxisResolution(
            diagnostics=(
                f"存在权重折算声明但提取侧覆盖不完整({detail}), 无法按折算轴核对总分",
            ),
        )
    if missing_declared:
        detail = ", ".join(missing_declared)
        return _AxisResolution(
            diagnostics=(
                f"存在权重折算声明但提取侧缺少声明组({detail}), 无法按折算轴核对总分",
            ),
        )
    folded = sum(score * weights[norm] for norm, score in aggregate.derived.items())
    return _AxisResolution(extracted_folded=round(folded, 4))


def _build_group_rows(
    pairs: tuple[DeclarationPair, ...], derived: dict[str, float], folded_axis: bool
) -> tuple[GroupReconciliation, ...]:
    """步骤d: 逐显式声明组对 vs 推导同组和; 无对应推导组时全额缺口(extracted=0)。

 folded_axis 现在表示"提取侧也已折算", 仅此时 declared 才取折算值——两侧同轴
 是硬约束(: declared 折算而 extracted 原始, 会产出恒等于未折算量的假缺口)。
 """
    rows: list[GroupReconciliation] = []
    for pair in pairs:
        declared = _pair_effective_score(pair) if folded_axis else pair.score
        extracted_sum = derived.get(pair.norm_label, 0.0)
        rows.append(
            GroupReconciliation(
                label=pair.label,
                norm_label=pair.norm_label,
                declared=round(declared, 4),
                extracted_sum=round(extracted_sum, 4),
                gap=round(extracted_sum - declared, 4),
                declared_source=pair.source,
            )
        )
    return tuple(rows)


def _undeclared_group_diagnostics(
    pairs: tuple[DeclarationPair, ...], derived: dict[str, float]
) -> tuple[str, ...]:
    """推导组未见于声明组对时输出 fail-visible 诊断(不裁决归属, 仅报警)。"""
    declared_norms = {pair.norm_label for pair in pairs}
    extras = sorted(norm for norm in derived if norm not in declared_norms)
    if not extras:
        return ()
    detail = ", ".join(f"{norm}={derived[norm]:g}" for norm in extras)
    return (f"提取组标签未见于声明组对: {detail}",)


def _collect_review_reasons(
    basis: _BasisResolution,
    total_gap: float | None,
    group_rows: tuple[GroupReconciliation, ...],
    multi_scheme_signal: str,
    capture: LedgerCapture,
    insufficiency_flags: tuple[str, ...],
) -> tuple[str, ...]:
    """步骤f: 收集全部 REVIEW_REQUIRED 理由(空即 RECONCILED, 不分叉)。"""
    reasons: list[str] = []
    if multi_scheme_signal:
        reasons.append(f"多方案信号: {multi_scheme_signal}")
    if capture.conflicts:
        reasons.append(f"捕获同域冲突未决{len(capture.conflicts)}条, 拒绝作为通过依据")
    if insufficiency_flags:
        reasons.append(f"证据充分性拒收{len(insufficiency_flags)}条")
    if basis.level not in (BasisLevel.L1_EXPLICIT_GROUP, BasisLevel.L3_DOC_TOTAL):
        reasons.append(f"基准层级{basis.level.value}不足以支撑通过判定")
    elif basis.low_confidence:
        reasons.append("基准低置信(默认100假设兜底), 需人工确认总分制")
    if total_gap is not None and abs(total_gap) > LEDGER_TOLERANCE:
        reasons.append(f"文档级缺口{total_gap:+g}超容差±{LEDGER_TOLERANCE:g}")
    gap_rows = [row for row in group_rows if abs(row.gap) > LEDGER_TOLERANCE]
    if gap_rows:
        detail = "; ".join(f"{row.label}{row.gap:+g}" for row in gap_rows)
        reasons.append(f"组级缺口超容差: {detail}")
    return tuple(reasons)


def _group_header_echo_reasons(
    entries: tuple[LedgerItemEntry, ...],
    pairs: tuple[DeclarationPair, ...],
    exempt_keys: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """: 组头行自证检测——提取项名是组语义形态时组轴 gap 恒 0 循环自证。

 这类行(如「技术标评分标准 35」)既是提取项又是声明组本身, 按构造不是叶子,
 计入通过依据即假绿; 检测委托 leaf_inventory 单源实现。: exempt_keys(网格提不出叶子的组)的组头形态计分行豁免。
 """
    from app.core.extractor.scoring.leaf_inventory import group_header_echo_names

    echo = group_header_echo_names(
        [(entry.name, entry.score) for entry in entries],
        {pair.norm_label for pair in pairs},
        exempt_norm_keys=set(exempt_keys),
    )
    if not echo:
        return ()
    preview = "、".join(echo[:5])
    return (f"组头行自证(未逐项展开, 不得作为叶子计分): {preview}",)


def reconcile_scoring_ledger(
    capture: LedgerCapture,
    item_entries: Sequence[LedgerItemEntry | tuple[str, float, str]],
    *,
    duplicate_exclusions: tuple[str, ...] = (),
    insufficiency_flags: tuple[str, ...] = (),
    extra_review_reasons: tuple[str, ...] = (),
    group_echo_exempt_keys: tuple[str, ...] = (),
) -> LedgerReport:
    """账本对账主流程: 双轴聚合->方案信号->基准分级->组级对账->状态判定。

 item_entries: `LedgerItemEntry` 序列(推荐, 携带 scheme/lot 作用域);
 亦兼容既有 (项名, 分值, 原文组标签) 三元组——但三元组不含方案身份, 对账器
 据此拒绝做方案类断言。
 duplicate_exclusions: 副本行排除记录(透传入报告备查);
 insufficiency_flags: 证据充分性拒收记录(非空即强制 REVIEW_REQUIRED);
 extra_review_reasons: 调用方注入的对账外强制复核理由(叶子覆盖缺口
 由 bridge 依据网格叶子清单计算后经此注入, 对账器保持通用不识叶子语义)。

 RECONCILED 仅当: L1/L3 高置信基准(显式捕获非默认100) 且 |total_gap|<=容差
 且 组级缺口全部在容差内 且 无多方案信号 且 无捕获冲突 且 无充分性拒收
 且 无组头行自证 且 无注入复核理由; 其余一律 REVIEW_REQUIRED
 (: 评估未完成与业务未采纳不分叉)。

 落点: 组头自证/叶子覆盖缺口并入 insufficiency_flags(结构化通道)——
 它们是不依赖基准置信度的确定性证据, 走 diagnostics 文本会被 verdict 公式的
 低置信软基准豁免短路, 3427 实测直绿正是该短路路径。
 """
    entries = coerce_ledger_item_entries(item_entries)
    leaf_integrity_flags = (
        *_group_header_echo_reasons(entries, capture.pairs, group_echo_exempt_keys),
        *extra_review_reasons,
    )
    insufficiency_flags = (*insufficiency_flags, *leaf_integrity_flags)
    aggregate = _aggregate_extraction(entries)
    derived = aggregate.derived
    multi_scheme_signal = _detect_multi_scheme_signal(capture, aggregate)
    basis = _classify_basis(capture, derived, aggregate.extracted_total)

    # 组级对账行: 只要有 >=2 条显式组声明就输出。: 此前把输出条件挂在
    # "L1 或多方案信号"上, 一旦方案信号按证据收口, 落 L2/L4 的文档会连组级缺口
    # 一并静默(2231 型 -4 缺口消失), 把误报换成漏报。
    axis = _resolve_folded_axis(capture, aggregate, basis)
    emit_rows = len(capture.pairs) >= 2
    group_rows = (
        _build_group_rows(capture.pairs, derived, axis.declared_folded) if emit_rows else ()
    )
    undeclared_diags = (
        _undeclared_group_diagnostics(capture.pairs, derived) if emit_rows else ()
    )

    # 文档级差额必须两侧同轴: 折算制取折算后的提取合计, 折不出来则不报差额。
    comparable_extracted = (
        axis.extracted_folded if basis.folded_axis else aggregate.extracted_total
    )
    total_gap = (
        round(comparable_extracted - basis.expected_total, 4)
        if basis.expected_total is not None and comparable_extracted is not None
        else None
    )
    axis_unresolved = basis.folded_axis and axis.extracted_folded is None
    basis = (
        _BasisResolution(
            basis.level, True, basis.expected_total, basis.folded_axis, basis.diagnostics
        )
        if axis_unresolved
        else basis
    )
    review_reasons = _collect_review_reasons(
        basis, total_gap, group_rows, multi_scheme_signal, capture, insufficiency_flags
    )
    status = LedgerStatus.REVIEW_REQUIRED if review_reasons else LedgerStatus.RECONCILED
    return LedgerReport(
        status=status,
        basis_level=basis.level,
        basis_low_confidence=basis.low_confidence,
        expected_total=basis.expected_total,
        extracted_total=round(aggregate.extracted_total, 4),
        total_gap=total_gap,
        group_rows=group_rows,
        unlabeled_sum=round(aggregate.unlabeled_sum, 4),
        extracted_total_folded=axis.extracted_folded,
        multi_scheme_signal=multi_scheme_signal,
        duplicate_exclusions=duplicate_exclusions,
        insufficiency_flags=insufficiency_flags,
        diagnostics=(
            *aggregate.diagnostics,
            *basis.diagnostics,
            *axis.diagnostics,
            *undeclared_diags,
            *review_reasons,
            *leaf_integrity_flags,  # 点名文本保持 fail-visible
        ),
    )


__all__ = ["reconcile_scoring_ledger"]
