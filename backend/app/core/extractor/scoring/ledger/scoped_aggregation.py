"""多 scope 顶层聚合与报告构建（Task6 结构整改）。

职责: 从已完成的各 scope 子报告聚合顶层守恒字段、生成 diagnostics 首条摘要、
 构建最终多 scope LedgerReport。
不做: 不做单 scope 对账、不过滤 chunk、不调用 LLM/DB。

设计约束（复杂度护栏）: 参数 <= 6，嵌套层级 <= 4。
"""
from __future__ import annotations

from app.core.extractor.scoring.ledger.contracts import (
    Applicability,
    BasisLevel,
    LEDGER_TOLERANCE,
    LeafIntegrityEvidence,
    LedgerReport,
    LedgerStatus,
    SCOPE_SENTINEL_LOTS,
    ScopedReport,
)


def build_scope_display_ids(scopes: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """O3: 按同 lot 多 scheme 上下文计算每个 scope 的显示标识。

    规则（对齐 display 约定，与 ScopedReport._default_scope_id 保持一致）:
      lot 为内部哨兵（__single__/__unassigned__） → scheme（哨兵不泄漏）。
      同 lot 多 scheme  → "scheme·lot"（保唯一）。
      单 scheme per lot → lot（单 scheme 最常见）。
    """
    lot_to_schemes: dict[str, set[str]] = {}
    for scheme, lot in scopes:
        if lot not in SCOPE_SENTINEL_LOTS:
            lot_to_schemes.setdefault(lot, set()).add(scheme)

    multi_scheme_lots = {
        lot_name for lot_name, s in lot_to_schemes.items() if len(s) > 1
    }

    result: dict[tuple[str, str], str] = {}
    for scope in scopes:
        scheme, lot = scope
        if lot in SCOPE_SENTINEL_LOTS:
            result[scope] = scheme          # 哨兵 → 显示 scheme
        elif lot in multi_scheme_lots:
            result[scope] = f"{scheme}·{lot}"  # 同 lot 多 scheme → 唯一标识
        else:
            result[scope] = lot             # 单 scheme per lot → lot
    return result


def determine_applicability(report: LedgerReport) -> Applicability:
    """O4: 基于对账结果质量判断 applicability，禁止假绿。

    阈值单源引用 contracts.LEDGER_TOLERANCE（当前 1.0 分），禁止在多处硬编码。

    条件（任一满足即返回 'unresolved'）:
      无权威基准（expected_total is None）；
      基准低置信（basis_low_confidence）；
      证据充分性不足（insufficiency_flags 非空）；
      未归卷量超容差（unlabeled_sum > LEDGER_TOLERANCE）。
    """
    if report.expected_total is None:
        return "unresolved"
    if report.basis_low_confidence:
        return "unresolved"
    if report.insufficiency_flags:
        return "unresolved"
    if report.unlabeled_sum > LEDGER_TOLERANCE:
        return "unresolved"
    return "applicable"


def build_top_level_diagnostics(
    scope_reports: list[ScopedReport],
    extra_diags: list[str],
) -> tuple[str, ...]:
    """O4: 构建顶层 diagnostics，首条包含每 scope 基准摘要并明确禁止全局比较。

    首条格式（严格，供前端识别）:
      "多 scope 模式（N 个 scope，全局比较已禁止）：scope1[basis=.. expected=..]；..."

    后续条目来自 extra_diags（I6 丢弃补充声明、C3 歧义排除等），
    以 dict.fromkeys 稳定去重后附加，子报告关键诊断可汇入但不重复。
    """
    scope_summaries = "; ".join(
        f"{sr.scope_id}[basis={sr.basis.value} expected={sr.expected_total}]"
        for sr in scope_reports
    )
    n = len(scope_reports)
    first = f"多 scope 模式（{n} 个 scope，全局比较已禁止）：{scope_summaries}"

    # 稳定去重（dict.fromkeys 保持插入顺序）
    unique_extra = list(dict.fromkeys(d for d in extra_diags if d))

    return (first, *unique_extra)


def _aggregate_leaf_integrity(scope_reports: list[ScopedReport]) -> LeafIntegrityEvidence:
    """ Step2: 聚合多 scope 叶子完整性证据。

 所有 scope 均 verified 且数量>=2 → per_scope_100；否则默认未验证。
 当前限制: 任一 scope 无叶清单（含无叶组豁免形态）均 fail-closed，不在此放宽。
 """
    if len(scope_reports) < 2:
        return LeafIntegrityEvidence()
    if not all(sr.leaf_integrity.verified for sr in scope_reports):
        return LeafIntegrityEvidence()
    total_inv = sum(sr.leaf_integrity.inventory_count for sr in scope_reports)
    total_matched = sum(sr.leaf_integrity.matched_count for sr in scope_reports)
    total_score = round(sum(sr.leaf_integrity.total for sr in scope_reports), 4)
    return LeafIntegrityEvidence(
        verified=True,
        basis="per_scope_100",
        inventory_count=total_inv,
        matched_count=total_matched,
        total=total_score,
    )


def aggregate_and_build_report(
    scope_reports: list[ScopedReport],
    total_extracted: float,
    extra_diags: list[str],
) -> LedgerReport:
    """C4: 聚合子报告守恒字段，构建多 scope 顶层 LedgerReport。

    守恒汇总（O5 精确等于子报告和）:
      unlabeled_sum  = sum(sr.unlabeled_sum for sr in scope_reports)
      insufficiency_flags = 各子报告 insufficiency_flags 之和（有序）
      multi_scheme_signal = 非空信号 join
    """
    agg_unlabeled = round(sum(sr.unlabeled_sum for sr in scope_reports), 4)
    agg_insufficiency = [f for sr in scope_reports for f in sr.insufficiency_flags]
    agg_multi = "; ".join(
        sr.multi_scheme_signal for sr in scope_reports if sr.multi_scheme_signal
    )
    diagnostics = build_top_level_diagnostics(scope_reports, extra_diags)
    leaf_ev = _aggregate_leaf_integrity(scope_reports)

    return LedgerReport(
        status=LedgerStatus.REVIEW_REQUIRED,
        basis_level=BasisLevel.L4_NONE,
        basis_low_confidence=True,
        expected_total=None,
        extracted_total=total_extracted,
        total_gap=None,
        unlabeled_sum=agg_unlabeled,
        insufficiency_flags=tuple(agg_insufficiency),
        multi_scheme_signal=agg_multi,
        diagnostics=diagnostics,
        scope_reports=tuple(scope_reports),
        global_comparison_allowed=False,
        leaf_integrity=leaf_ev,
    )


__all__ = [
    "aggregate_and_build_report",
    "build_scope_display_ids",
    "build_top_level_diagnostics",
    "determine_applicability",
]
