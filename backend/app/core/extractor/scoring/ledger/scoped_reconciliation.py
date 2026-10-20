"""多 scope 协调器（Task6 结构整改: <150 行，参数<=6）。

职责: 接收 ScopedLedgerRequest，分区条目 → 逐 scope 调用 reconcile_scoring_ledger
 → 委托 scope_filtering/scoped_aggregation 完成过滤与聚合。
不做: 不实现对账算法、不实现 chunk 过滤逻辑（委托 scope_filtering）、不调用 LLM/DB。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace

from loguru import logger

from app.core.extractor.scoring.ledger.contracts import (
    Applicability,
    LedgerItemEntry,
    LedgerReport,
    ScopeIdentity,
    ScopedReport,
)
from app.core.extractor.scoring.ledger.declaration_capture import (
    capture_scoring_declarations,
)
from app.core.extractor.scoring.ledger.declaration_scope import (
    extract_formula_total_candidates,
)
from app.core.extractor.scoring.ledger.evidence_sufficiency import (
    assess_item_evidence_sufficiency,
)
from app.core.extractor.scoring.leaf_integrity import assess_leaf_integrity
from app.core.extractor.scoring.ledger.ledger_reconciliation import (
    reconcile_scoring_ledger,
)
from app.core.extractor.scoring.ledger.remainder_attribution import (
    attribute_unique_remainder,
)
from app.core.extractor.scoring.ledger.scoped_aggregation import (
    aggregate_and_build_report,
    build_scope_display_ids,
    determine_applicability,
)
from app.core.extractor.scoring.ledger.scoped_contracts import DEFAULT_SCOPE
from app.core.extractor.scoring.ledger.scope_filtering import (
    ScopeFilterContext,
    filter_grids_for_scope,
    filter_pages_for_scope,
)
from app.core.extractor.scoring.scoring_facts import ScoringScopeKey

_SINGLE_LOT = ScoringScopeKey.SINGLE_LOT
_SINGLE_SCHEME = ScoringScopeKey.SINGLE_SCHEME
_UNASSIGNED = ScoringScopeKey.UNASSIGNED_LOT
_UNLABELED_TOL = 1.0


@dataclass(frozen=True)
class ScopedLedgerRequest:
    """多 scope 对账请求（frozen 收敛 coordinate 入参）。"""

    pages: tuple[tuple[int, str], ...]
    chunk_scopes: tuple[tuple[str | None, str | None], ...]
    grids: tuple[tuple[int, list[list[str]]], ...]
    grid_scopes: tuple[tuple[str | None, str | None], ...]
    entries_with_rules: tuple[tuple[LedgerItemEntry, str], ...]
    declaration_pages: tuple[tuple[int, str], ...] = ()
    parallel_scope_labels: tuple[str, ...] = ()
    document_id: int = 0
    # 装配前捕获的声明样表行（无 scope 归属，与 declaration_pages 同类处置）
    declaration_grids: tuple[tuple[int, list[list[str]]], ...] = ()
    # bridge 依据网格叶子清单注入的强制复核理由（单 scope 才消费,
    # 多 scope 下叶子清单无 scope 归属证据, 与 declaration_* 同源政策不广播）
    extra_review_reasons: tuple[str, ...] = ()
    # 网格提不出叶子的组归一键——组头形态计分行豁免自证检测
    group_echo_exempt_keys: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 内部工具（小函数，<=150 行总预算）
# ---------------------------------------------------------------------------

def _entry_scope(entry: LedgerItemEntry) -> ScopeIdentity:
    lot = (entry.lot or "").strip()
    lot = lot if lot == _UNASSIGNED else (lot or _SINGLE_LOT)
    return ((entry.scheme or "").strip() or _SINGLE_SCHEME, lot)


def _partition(
    ewrs: tuple[tuple[LedgerItemEntry, str], ...],
) -> dict[ScopeIdentity, list[tuple[LedgerItemEntry, str]]]:
    out: dict[ScopeIdentity, list[tuple[LedgerItemEntry, str]]] = defaultdict(list)
    for entry, rule in ewrs:
        out[_entry_scope(entry)].append((entry, rule))
    return dict(out)


def _doc_named_schemes(partitions: dict) -> set[str]:
    return {s for s, _ in partitions if s != _SINGLE_SCHEME}


@dataclass(frozen=True)
class _ScopePipelineInput:
    """单 scope 对账管道入参（护栏: 参数>6 收敛为 dataclass）。"""

    ewrs: list[tuple[LedgerItemEntry, str]]
    pages: list[tuple[int, str]]
    grids: list[tuple[int, list[list[str]]]]
    decl_pages: tuple[tuple[int, str], ...] = ()
    labels: tuple[str, ...] = ()
    extra_review_reasons: tuple[str, ...] = ()
    group_echo_exempt_keys: tuple[str, ...] = ()
    # I3: 仅供叶子完整性评估的网格（不含无 scope 的 declaration_grids）
    # None 时回退到 grids（多 scope 路径已通过 filter_grids_for_scope 隔离，行为等价）
    integrity_grids: list[tuple[int, list[list[str]]]] | None = None


def _reconcile_scope(inp: _ScopePipelineInput) -> LedgerReport:
    """单 scope 完整管道: capture → 公式注入 → sufficiency → attribution → reconcile。"""
    entries = tuple(e for e, _ in inp.ewrs)
    rule_texts = [(e.name, e.score, r) for e, r in inp.ewrs]
    capture = capture_scoring_declarations(
        inp.pages, inp.grids,
        supplementary_pages=list(inp.decl_pages),
        parallel_scope_labels=list(inp.labels),
    )
    formula = extract_formula_total_candidates([t for _, t in inp.pages])
    if formula:
        combined = tuple(sorted(set(capture.total_candidates) | set(formula)))
        capture = replace(capture, total_candidates=combined)
    _, flags = assess_item_evidence_sufficiency(rule_texts)
    attr = attribute_unique_remainder(capture, entries)
    report = reconcile_scoring_ledger(
        capture, attr.entries, insufficiency_flags=flags,
        extra_review_reasons=inp.extra_review_reasons,
        group_echo_exempt_keys=inp.group_echo_exempt_keys,
    )
    if attr.diagnostic:
        report = replace(report,
            inferred_group_label=attr.target_label,
            inferred_group_sum=attr.attributed_sum,
            inferred_group_count=attr.attributed_count,
            diagnostics=(attr.diagnostic, *report.diagnostics),
        )
    # I3: 叶子完整性仅使用 integrity_grids（不含无 scope 的 declaration_grids）
    eff_grids = inp.integrity_grids if inp.integrity_grids is not None else inp.grids
    raw_grids = [g for _, g in eff_grids]
    leaf_ev = assess_leaf_integrity(raw_grids, entries, report)
    return replace(report, leaf_integrity=leaf_ev)


def _to_scoped(
    scope: ScopeIdentity, report: LedgerReport,
    applicability: Applicability, extra: tuple[str, ...], display_id: str,
) -> ScopedReport:
    ebv = tuple((r.norm_label, r.declared) for r in report.group_rows if r.declared > 0)
    return ScopedReport(
        scope=scope, status=report.status, basis=report.basis_level,
        expected_total=report.expected_total, extracted_total=report.extracted_total,
        total_gap=report.total_gap, expected_by_volume=ebv,
        applicability=applicability,
        basis_low_confidence=report.basis_low_confidence,
        diagnostics=(*report.diagnostics, *extra),
        unlabeled_sum=report.unlabeled_sum,
        insufficiency_flags=report.insufficiency_flags,
        multi_scheme_signal=report.multi_scheme_signal,
        scope_display_id=display_id,
        leaf_integrity=report.leaf_integrity,
    )


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def coordinate_scoped_ledger(request: ScopedLedgerRequest) -> LedgerReport:
    """分区 → 逐 scope 对账 → 聚合报告（单入参，零行为回归）。"""
    partitions = _partition(request.entries_with_rules)

    if len(partitions) <= 1:  # 单 scope 路径（向后兼容）
        scope = next(iter(partitions), DEFAULT_SCOPE)
        ewrs = partitions.get(scope, list(request.entries_with_rules))
        return _reconcile_scope(_ScopePipelineInput(
            ewrs=ewrs, pages=list(request.pages),
            grids=list(request.grids) + list(request.declaration_grids),
            integrity_grids=list(request.grids),  # I3: 声明网格只供 capture，不进 leaf ev
            decl_pages=request.declaration_pages,
            labels=request.parallel_scope_labels,
            extra_review_reasons=request.extra_review_reasons,
            group_echo_exempt_keys=request.group_echo_exempt_keys,
        ))

    # 多 scope 路径
    lot_schemes: dict[str, set[str]] = defaultdict(set)
    for s, lot_key in partitions:
        if lot_key not in (_SINGLE_LOT, _UNASSIGNED):
            lot_schemes[lot_key].add(s)
    doc_schemes = _doc_named_schemes(partitions)

    all_scopes = sorted(partitions.keys())
    display_ids = build_scope_display_ids(all_scopes)

    extra_diags: list[str] = []
    if request.declaration_pages:
        n = sum(1 for _, p in request.declaration_pages if p.strip())
        if n:
            extra_diags.append(f"多 scope 模式：已丢弃 {n} 页补充声明（无 scope，不广播）")
    if request.declaration_grids:
        # 与 declaration_pages 同源政策: 无 scope 归属的证据在多 scope 下不广播,
        # 广播会让某一 scope 的声明去和另一 scope 的提取项对账
        extra_diags.append(
            f"多 scope 模式：已丢弃 {len(request.declaration_grids)} 行声明表"
            "（无 scope，不广播）"
        )

    scope_reports: list[ScopedReport] = []
    total_extracted = 0.0

    for scope in all_scopes:
        ewrs = partitions[scope]
        s_scheme, s_lot = scope
        is_unassigned = s_lot == _UNASSIGNED
        is_no_lot = s_lot == _SINGLE_LOT

        ctx = ScopeFilterContext(
            target_scope=scope,
            lot_has_single_scheme=len(lot_schemes.get(s_lot, set())) == 1,
            doc_has_single_named_scheme=len(doc_schemes) == 1,
            is_single_scope=False,
        )
        scope_pages, scope_diags = filter_pages_for_scope(
            ctx, list(request.pages), list(request.chunk_scopes))
        scope_grids, grid_diags = filter_grids_for_scope(
            ctx, list(request.grids), list(request.grid_scopes))
        scope_diags = list(dict.fromkeys(scope_diags + grid_diags))

        report = _reconcile_scope(_ScopePipelineInput(
            ewrs=ewrs, pages=scope_pages, grids=scope_grids,
        ))

        app: Applicability
        if is_no_lot or is_unassigned:
            app = "unresolved"
            scope_diags.append(f"scope {scope}：lot={s_lot!r} 无权威基准→unresolved")
        elif not scope_pages:
            app = "unresolved"
            scope_diags.append(f"scope {scope}：无匹配 chunk→unresolved")
        else:
            app = determine_applicability(report)
            if app == "unresolved":
                scope_diags.append(f"scope {scope}：质量不足→unresolved")

        sr = _to_scoped(scope, report, app, tuple(scope_diags), display_ids.get(scope, ""))
        scope_reports.append(sr)
        total_extracted = round(total_extracted + report.extracted_total, 4)
        extra_diags.extend(scope_diags)

        logger.debug("[scoped] doc={} scope={} status={} expected={} app={}",
                     request.document_id, scope, report.status.value,
                     report.expected_total, app)

    # dict.fromkeys 稳定去重，避免跨 scope 重复诊断指数膨胀
    unique_extra = list(dict.fromkeys(d for d in extra_diags if d))
    return aggregate_and_build_report(scope_reports, total_extracted, unique_extra)


__all__ = ["ScopedLedgerRequest", "coordinate_scoped_ledger"]
