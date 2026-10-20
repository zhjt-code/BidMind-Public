"""评分三态判绿权威源（verdict 单源）。

职责: 以 legacy 满分核计载荷 + 账本对账报告为输入, 推导评分三态 verdict
 (green/blue/amber), 供 VLM 救援触发与确认页横幅消费同一结论。
不做: 不生成用户文案、不修改报告内容、不调用 LLM、不渲染 UI;
 DB 访问仅限 resolve_document_trigger_verdict 读取文档声明信号。

公式与前端 ScoringTotalsBanner 三态逐项对齐（豁免链: 折算假差额 /
软基准自洽 / 组构成让位 -776 / 多包 scope 三态 -803）:

 isOk = not effectiveReportNeedsReview and not effectiveLedgerNeedsReview
 and not scopeAmber and not isScopeInfoOnly

任一子谓词改动必须同步核对前端实现（requirementReviewTransforms.ts /
scoringScopeReportTransforms.ts / scoringGroupShares.ts），并更新本模块防御测试。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app.core.extractor.scoring.ledger.contracts import (
    BasisLevel,
    LedgerReport,
    LedgerStatus,
    ScopedReport,
)
from app.core.extractor.scoring.score_totals import (
    ScoringTotalsEntry,
    build_totals_report_payload,
)

VERDICT_GREEN = "green"
VERDICT_BLUE = "blue"
VERDICT_AMBER = "amber"

# 与前端 SCOPE_SCORE_TOLERANCE / COVERAGE_TOLERANCE 同值（0.01）
_TOLERANCE = 0.01


@dataclass(frozen=True)
class _FoldedBasis:
    """折算制量化口径（对齐前端 ScoringFoldedBasis）。"""

    folded_base: float
    raw_expected_total: float


def _folded_basis(payload: dict[str, Any]) -> Optional[_FoldedBasis]:
    """port: requirementReviewTransforms.buildFoldedScoringBasis。"""
    if payload.get("needs_review") or payload.get("weight_sum_ok") is False:
        return None
    by_lot = payload.get("by_lot") or {}
    if isinstance(by_lot, dict) and len(by_lot) > 1:
        return None
    raw_checks = payload.get("volume_checks") or {}
    checks = [
        c
        for c in raw_checks.values()
        if isinstance(c, dict)
        and c.get("within")
        and float(c.get("weight") or 0) > 0
        and float(c.get("sum") or 0) > 0
    ]
    if len(checks) < 2:
        return None
    weight_total = sum(float(c.get("weight") or 0) for c in checks)
    if abs(weight_total - 1) > 0.02:
        return None
    expected_total = sum(float(c.get("expected") or 0) for c in checks)
    if abs(expected_total - 100) <= 1:
        return None
    folded_base = sum(
        float(c.get("expected") or 0) * float(c.get("weight") or 0) for c in checks
    )
    return _FoldedBasis(
        folded_base=round(folded_base, 4),
        raw_expected_total=round(expected_total, 4),
    )


def _ledger_gap_is_folding_artifact(
    folded: Optional[_FoldedBasis], ledger: Optional[LedgerReport]
) -> bool:
    """port: isLedgerGapFoldingArtifact（折算假差额豁免）。"""
    if folded is None or ledger is None:
        return False
    if ledger.expected_total is None or ledger.total_gap is None:
        return False
    if abs(folded.folded_base - ledger.expected_total) > _TOLERANCE:
        return False
    folding_artifact = folded.raw_expected_total - folded.folded_base
    return abs(ledger.total_gap - folding_artifact) <= _TOLERANCE


def _ledger_review_reason(ledger: LedgerReport) -> str:
    """port: resolveLedgerReviewReason（六类原因优先级）。"""
    if ledger.total_gap is not None and abs(ledger.total_gap) > _TOLERANCE:
        return "total_gap"
    if any(abs(row.gap) > _TOLERANCE for row in ledger.group_rows):
        return "group_gap"
    if ledger.multi_scheme_signal:
        return "multi_scheme"
    if ledger.unlabeled_sum > _TOLERANCE:
        return "unlabeled"
    if len(ledger.insufficiency_flags) > 0:
        return "insufficiency"
    if ledger.basis_low_confidence or ledger.basis_level is BasisLevel.L2_DERIVED_GROUP:
        return "low_confidence"
    if ledger.basis_level is BasisLevel.L4_NONE:
        return "low_confidence"
    return "other"


def _ledger_soft_basis_only(ledger: Optional[LedgerReport]) -> bool:
    """port: isLedgerSelfConsistentWithSoftBasis（软基准自洽豁免）。"""
    if ledger is None:
        return False
    if _ledger_review_reason(ledger) != "low_confidence":
        return False
    if ledger.expected_total is None:
        return False
    return ledger.total_gap is not None and abs(ledger.total_gap) <= _TOLERANCE


def _ledger_group_shares_complete(ledger: Optional[LedgerReport]) -> bool:
    """port: ledgerGroupShares 非空判定（组构成完整覆盖门禁）。"""
    if ledger is None or not ledger.group_rows:
        return False
    if abs(ledger.unlabeled_sum) > _TOLERANCE:
        return False
    covered = sum(row.extracted_sum for row in ledger.group_rows)
    if abs(covered - ledger.extracted_total) > _TOLERANCE:
        return False
    return any(
        str(row.label or "").strip() and row.extracted_sum > 0
        for row in ledger.group_rows
    )


def _is_misclassified_applicable(scope: ScopedReport) -> bool:
    """port: isMisclassifiedApplicable（误降级 unresolved 判定）。"""
    return (
        scope.applicability == "unresolved"
        and scope.expected_total is not None
        and not scope.basis_low_confidence
        and len(scope.insufficiency_flags) == 0
        and scope.unlabeled_sum <= _TOLERANCE
    )


def _scope_soft_self_consistent(scope: ScopedReport) -> bool:
    """scope 级软基准自洽豁免（, 与单包 豁免同构）。

 该 scope 分值完全自洽（基准非空且缺口/未归组≈0、无不足项、无多方案信号），
 唯一疑点仅剩"基准低置信/适用关系未确认"——按提醒而非蓝色阻断呈现。
 """
    return (
        scope.expected_total is not None
        and scope.total_gap is not None
        and abs(scope.total_gap) <= _TOLERANCE
        and scope.unlabeled_sum <= _TOLERANCE
        and len(scope.insufficiency_flags) == 0
        and not scope.multi_scheme_signal
    )


def _scope_flags(scopes: Iterable[ScopedReport]) -> tuple[bool, bool, bool]:
    """port: buildScopeReportView 三面旗（anyApplicableGap/anyMisclassifiedGap/anyUnresolved）。"""
    scope_list = list(scopes)
    applicable = [s for s in scope_list if s.applicability == "applicable"]
    any_applicable_gap = any(
        (s.total_gap is not None and abs(s.total_gap) > _TOLERANCE)
        or len(s.insufficiency_flags) > 0
        or s.unlabeled_sum > _TOLERANCE
        for s in applicable
    )
    any_misclassified_gap = any(
        _is_misclassified_applicable(s)
        and s.total_gap is not None
        and abs(s.total_gap) > _TOLERANCE
        for s in scope_list
    )
    # 软自洽 scope 不驱动蓝色阻断（单包 豁免的 scope 级对称）
    any_unresolved = any(
        (
            s.applicability == "unresolved"
            or (s.applicability == "applicable" and s.basis_low_confidence)
        )
        and not _scope_soft_self_consistent(s)
        for s in scope_list
    )
    return any_applicable_gap, any_misclassified_gap, any_unresolved


def _leaf_integrity_strong(
    ledger: LedgerReport,
    totals_payload: Optional[dict[str, Any]],
) -> bool:
    """ 步骤3A: 叶子完整性强证据门禁。

 职责: 判断叶子级一一映射证据是否足够强，以豁免声明层噪音。
 不做: 不修改账本/载荷、不判颜色逻辑之外的路径。
 """
    evidence = ledger.leaf_integrity
    if not evidence.verified:
        return False
    # 账本层拒收标记优先（insufficiency/多方案歧义不可豁免）
    if ledger.insufficiency_flags:
        return False
    if ledger.multi_scheme_signal:
        return False
    # 载荷存在时缺失计数必须均为0
    if totals_payload is not None:
        if int(totals_payload.get("missing_score_count") or 0) != 0:
            return False
        if int(totals_payload.get("missing_rule_count") or 0) != 0:
            return False
        if bool(totals_payload.get("structure_review_required", False)):
            return False
    # basis 口径双向严格校验（复审）：
    #   单 scope（global_comparison_allowed=True）→ 仅接受 single_100
    #   多 scope（global_comparison_allowed=False）→ 仅接受 per_scope_100
    if ledger.global_comparison_allowed:
        return evidence.basis == "single_100"
    return evidence.basis == "per_scope_100"


def resolve_scoring_verdict(
    totals_payload: Optional[dict[str, Any]],
    ledger: Optional[LedgerReport],
) -> Optional[str]:
    """推导评分三态 verdict；两份输入均缺失返回 None（前端此时不渲染横幅）。"""
    if totals_payload is None and ledger is None:
        return None

    report_needs_review = bool((totals_payload or {}).get("needs_review"))
    structure_review_required = bool(
        (totals_payload or {}).get("structure_review_required", False)
    )
    folded = _folded_basis(totals_payload) if totals_payload else None
    folding_artifact = _ledger_gap_is_folding_artifact(folded, ledger)
    soft_basis_only = _ledger_soft_basis_only(ledger)
    ledger_reconciled_ok = (
        ledger is not None
        and ledger.status is LedgerStatus.RECONCILED
        and not ledger.basis_low_confidence
    )
    ledger_needs_review = (
        ledger is not None
        and not ledger_reconciled_ok
        and not folding_artifact
        and not soft_basis_only
    )
    global_allowed = ledger.global_comparison_allowed if ledger is not None else True
    scope_view_active = ledger is not None and not global_allowed
    scope_amber = False
    scope_info_only = False
    if scope_view_active and ledger is not None:
        any_gap, any_mis, any_unres = _scope_flags(ledger.scope_reports)
        scope_amber = any_gap or any_mis
        scope_info_only = (not scope_amber) and any_unres

    checks = (totals_payload or {}).get("volume_checks") or {}
    problem_volume_count = sum(
        1 for c in checks.values() if isinstance(c, dict) and not c.get("within", False)
    )
    legacy_soft_basis_superseded = (
        totals_payload is not None
        and soft_basis_only
        and int((totals_payload or {}).get("missing_rule_count") or 0) == 0
        and int((totals_payload or {}).get("missing_score_count") or 0) == 0
        and problem_volume_count == 0
    )
    legacy_review_superseded = not structure_review_required and (
        (
            report_needs_review
            and not ledger_needs_review
            and (_ledger_group_shares_complete(ledger) or legacy_soft_basis_superseded)
        )
        or (report_needs_review and scope_view_active)
    )

    effective_report_needs_review = report_needs_review and not legacy_review_superseded
    effective_ledger_needs_review = ledger_needs_review if global_allowed else False

    # 步骤3A: 叶子级一一映射强证据豁免声明层噪音
    leaf_strong = (
        _leaf_integrity_strong(ledger, totals_payload)
        if ledger is not None
        else False
    )
    # 2026-08-31 用户裁决(pdf_direct): 细项不重复且各卷分值闭合即判绿——
    # PDF自身业务语义(条目盖章的卷基准)优先, chunk声明捕获的账本噪音让位;
    # 缺评审标准不阻断绿(missing_rule 仅保留提示), 结构丢失信号仍阻断。
    # 宪法附则(同款): 多方案信号对"单方案口径的闭合"保留否决权——
    # 模型整套漏提方案时 entries 单方案闭合是假象; 但 basis=per_scheme
    # (条目已带方案轴且逐方案闭合)时方案轴已被承接, 信号不再阻断(3484)。
    closure = (totals_payload or {}).get("volume_closure") or {}
    closure_strong = (
        bool(closure.get("verified"))
        and not structure_review_required
        and not (
            ledger is not None
            and ledger.multi_scheme_signal
            and closure.get("basis") != "per_scheme"
        )
    )

    is_ok = leaf_strong or closure_strong or not (
        effective_report_needs_review
        or effective_ledger_needs_review
        or scope_amber
        or scope_info_only
    )
    if is_ok:
        return VERDICT_GREEN
    has_other_real_error = (
        effective_report_needs_review or effective_ledger_needs_review or scope_amber
    )
    if scope_info_only and not has_other_real_error:
        return VERDICT_BLUE
    return VERDICT_AMBER


def scoring_totals_entries_from_items(items: Iterable[Any]) -> list[ScoringTotalsEntry]:
    """提取项(ExtractedScoringItem 形态) -> 满分校验入参。

    字段映射与 requirement_service._build_scoring_totals_report 对齐
    （penalty/bonus 调整项不计入、has_rules 取两类规则并集），保证提取时
    verdict 与确认页 verdict 在同一批评分项上口径一致。
    is_llm_source 仅影响展示计数不参与 verdict，提取侧统一置 False。
    """
    entries: list[ScoringTotalsEntry] = []
    for item in items:
        if str(getattr(item, "fact_type", None) or "base_score") in ("penalty", "bonus"):
            continue
        rules: list[Any] = [
            *(getattr(item, "score_rules", None) or []),
            *(getattr(item, "deduction_rules", None) or []),
        ]
        has_rules = any(str(rule or "").strip() for rule in rules)
        description = getattr(item, "description", None)
        entries.append(
            ScoringTotalsEntry(
                score_weight=float(getattr(item, "score_weight", 0) or 0),
                volume=getattr(item, "volume", None),
                volume_label=getattr(item, "volume_label", None),
                lot=getattr(item, "lot", None),
                scheme=getattr(item, "scheme", None),
                volume_weight=getattr(item, "volume_weight", None),
                volume_max=getattr(item, "volume_max", None),
                source_page=getattr(item, "source_page", None),
                has_rules=has_rules,
                description_length=(
                    len(str(description or "").strip()) if description is not None else None
                ),
                is_llm_source=False,
                diagnostic_name=str(getattr(item, "score_item_name", "") or "")[:80],
            )
        )
    return entries


def resolve_extraction_trigger_verdict(
    *,
    chunks: list[Any],
    items: list[Any],
    diagnostics: list[str],
    document_id: int,
    scoring_signals: Any,
) -> str:
    """提取尾部 VLM 触发判定: 与确认页同法复算 legacy 载荷 + 账本后取 verdict。

    双输入均缺失(零提取)按 amber 处理——"什么都没提出来"不可能是绿。
    """
    from app.core.extractor.scoring.ledger.declaration_capture import (
        declaration_grids_from_signals,
        declaration_pages_from_signals,
    )
    from app.core.extractor.scoring.ledger.extractor_bridge import (
        observe_scoring_ledger_shadow,
    )

    entries = scoring_totals_entries_from_items(items)
    extra = [d for d in dict.fromkeys(str(x) for x in (diagnostics or [])) if d.strip()]
    totals_payload = build_totals_report_payload(entries, extra_diagnostics=extra)
    ledger = observe_scoring_ledger_shadow(
        list(chunks),
        list(items),
        document_id=document_id,
        declaration_pages=declaration_pages_from_signals(scoring_signals),
        declaration_grids=declaration_grids_from_signals(scoring_signals),
        log_summary=False,
    )
    verdict = resolve_scoring_verdict(totals_payload, ledger)
    return verdict or VERDICT_AMBER


async def resolve_document_trigger_context(
    *,
    db: Any,
    document_id: int,
    chunks: list[Any],
    items: list[Any],
    diagnostics: list[str],
) -> tuple[str, Optional[LedgerReport]]:
    """读取文档声明信号后执行触发判定, 返回 (verdict, 账本核心报告)。

 声明信号与确认页 build_scoring_ledger_report 同源——不补喂时
 提取侧可能 reconciled 而 API 侧 review_required, 造成 VLM 漏触发。
 账本报告供裁判上下文装配复用(审查V2: 高置信文档级基准), 避免二次对账。
 """
    from sqlalchemy import select

    from app.core.extractor.scoring.ledger.declaration_capture import (
        declaration_grids_from_signals,
        declaration_pages_from_signals,
    )
    from app.core.extractor.scoring.ledger.extractor_bridge import (
        observe_scoring_ledger_shadow,
    )

    from app.models.document import Document

    signals = (
        await db.execute(
            select(Document.scoring_signals).where(Document.id == document_id)
        )
    ).scalar_one_or_none()
    entries = scoring_totals_entries_from_items(items)
    extra = [d for d in dict.fromkeys(str(x) for x in (diagnostics or [])) if d.strip()]
    totals_payload = build_totals_report_payload(entries, extra_diagnostics=extra)
    ledger = observe_scoring_ledger_shadow(
        list(chunks),
        list(items),
        document_id=document_id,
        declaration_pages=declaration_pages_from_signals(signals),
        declaration_grids=declaration_grids_from_signals(signals),
        log_summary=False,
    )
    verdict = resolve_scoring_verdict(totals_payload, ledger)
    return (verdict or VERDICT_AMBER), ledger


async def resolve_document_trigger_verdict(
    *,
    db: Any,
    document_id: int,
    chunks: list[Any],
    items: list[Any],
    diagnostics: list[str],
) -> str:
    """触发判定薄封装(仅需 verdict 的调用方使用)。"""
    verdict, _ledger = await resolve_document_trigger_context(
        db=db, document_id=document_id, chunks=chunks,
        items=items, diagnostics=diagnostics,
    )
    return verdict


__all__ = [
    "VERDICT_AMBER",
    "VERDICT_BLUE",
    "VERDICT_GREEN",
    "resolve_document_trigger_context",
    "resolve_document_trigger_verdict",
    "resolve_extraction_trigger_verdict",
    "resolve_scoring_verdict",
    "scoring_totals_entries_from_items",
]
