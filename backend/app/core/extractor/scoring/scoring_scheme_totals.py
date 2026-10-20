"""多评分方案核计报告（, 阶段2）。

职责: 把携带 scheme 标识的评分项条目按方案分组独立核计, 组装确认页/核计横幅 payload。
不做: 不做单方案核计(score_totals.build_totals_report_payload 职责)、不读 ORM、
 不做 scheme 切分(scheme_segmentation 职责)。

定位: 与 score_totals._build_lot_totals_report_payload 同构的方案维度分组层;
 独立成模块防止向已超 Soft 阈值的 score_totals.py 继续堆叠(复杂度护栏)。
"""

from __future__ import annotations

from typing import Any

from app.core.extractor.scoring.scheme_segmentation import scheme_display_label


def build_scheme_totals_report_payload(
    entries: list[Any],
    scheme_keys: list[str],
    *,
    extra_diagnostics: list[str] | None = None,
    structure_review_required: bool = False,
) -> dict[str, Any]:
    """按评分方案分组核计(每方案内部沿用单方案/多包核计口径)。

 未归属方案的条目计入全集并单独提示(教训: 分组聚合禁止静默丢弃成员)。
 """
    from app.core.extractor.scoring.score_totals import (
        ScoringTotalsEntry,
        build_totals_report_payload,
    )

    total_schemes = len(scheme_keys)
    by_scheme: dict[str, float] = {}
    diagnostics: list[str] = []
    scheme_status: list[str] = []
    needs_review = False
    missing_total = 0
    missing_rule_total = 0
    item_count = 0
    by_volume_global: dict[str, float] = {}
    llm_count = sum(1 for e in entries if getattr(e, "is_llm_source", False))
    authority_count = max(len(entries) - llm_count, 0)

    for scheme in scheme_keys:
        group = [e for e in entries if e.scheme == scheme]
        stripped = [
            ScoringTotalsEntry(
                score_weight=e.score_weight,
                volume=e.volume,
                volume_label=e.volume_label,
                lot=e.lot,
                volume_weight=e.volume_weight,
                volume_max=e.volume_max,
                source_page=e.source_page,
                has_rules=e.has_rules,
                description_length=e.description_length,
                is_llm_source=e.is_llm_source,
                diagnostic_name=e.diagnostic_name,
            )
            for e in group
        ]
        payload = build_totals_report_payload(stripped)
        if payload is None:
            continue
        label = scheme_display_label(scheme, total_schemes=total_schemes) or scheme
        scheme_total = round(sum(float(e.score_weight or 0) for e in group), 4)
        by_scheme[label] = scheme_total
        item_count += int(payload.get("item_count", 0) or 0)
        missing_total += int(payload.get("missing_score_count", 0) or 0)
        missing_rule_total += int(payload.get("missing_rule_count", 0) or 0)
        for volume, score in payload.get("by_volume", {}).items():
            by_volume_global[volume] = round(
                by_volume_global.get(volume, 0.0) + float(score or 0), 4
            )
        scheme_failed = bool(payload.get("needs_review"))
        scheme_status.append(
            f"{label} {scheme_total}/100.0（{'未通过' if scheme_failed else '通过'}）"
        )
        if scheme_failed:
            needs_review = True
            lines = [d for d in payload.get("diagnostics", []) if d]
            if not lines:
                lines = [payload.get("note") or "评分项满分校验未通过"]
            for line in dict.fromkeys(lines):
                diagnostics.append(f"{label}: {line}")

    unassigned = [e for e in entries if not e.scheme]
    if unassigned:
        needs_review = True
        unassigned_total = round(sum(float(e.score_weight or 0) for e in unassigned), 4)
        item_count += len(unassigned)
        diagnostics.append(
            f"另有 {len(unassigned)} 项评分项（合计 {unassigned_total} 分）未识别评分方案归属，"
            f"未计入任何方案核计，请人工核对"
        )

    if structure_review_required:
        needs_review = True

    if needs_review and scheme_status:
        diagnostics.insert(0, "各评分方案核计：" + "；".join(scheme_status))

    if not needs_review:
        note = "按评分方案核计通过（" + "；".join(scheme_status) + "）"
    else:
        note = (
            "评分表部分内容未能完整验收，请对照原文核对"
            if structure_review_required
            else "按评分方案核计未通过"
        )
        if not diagnostics:
            diagnostics.append(note)
        if extra_diagnostics:
            diagnostics = list(dict.fromkeys([
                *diagnostics,
                *(str(x).strip() for x in extra_diagnostics if str(x).strip()),
            ]))

    return {
        "needs_review": needs_review,
        "note": note,
        "by_volume": by_volume_global,
        "by_lot": by_scheme,
        "volume_checks": {},
        "weight_sum_ok": True,
        "item_count": item_count,
        "missing_score_count": missing_total,
        "missing_rule_count": missing_rule_total,
        "authority_count": authority_count,
        "llm_count": llm_count,
        "structure_review_required": structure_review_required,
        "diagnostics": diagnostics,
    }


__all__ = ["build_scheme_totals_report_payload"]
