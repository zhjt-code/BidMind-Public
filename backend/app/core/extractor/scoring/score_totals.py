"""评分项分值合计校验（P2 完整性护栏 + 按卷校验）。

职责: 汇总评分项满分，与期望基准对比，给出"是否需人工复核"信号。
 - 无卷定义: 全局校验(满分合计应=100, 单一100分制)。
 - 有卷定义: 按卷校验(每卷子项满分和应=卷满分, 卷权重和应≈100%)。
不做: 不修改评分项、不调用 LLM/DB; 仅做确定性数值校验。

定位: 跨页拼表 + LLM 提取后, 用"满分合计应=基准"做确定性兜底, 不一致即标待人工复核,
 避免漏行/串行导致的静默缺失。多卷/加权制下全局100已失效, 改按卷校验。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from app.core.extractor.scoring.scoring_decision_trace import record as _trace
from app.core.extractor.scoring.scoring_diagnostic_policy import diagnostics_require_review
from app.core.extractor.scoring.scoring_item_naming import strip_scoring_name_decoration

# 卷标识 → 中文卷名(: 与 review_contracts._VOLUME_LABELS/前端 VOLUME_LABELS 对齐)。
# score_totals 是无 DB/LLM 依赖的纯数值校验模块(见模块 docstring), 不引入 review_contracts
# (其依赖 sqlalchemy/models)以免破坏分层与可能的循环导入(同 教训), 故本地维护同名映射。
_VOLUME_LABELS: dict[str, str] = {
    "technical": "技术标",
    "price": "价格",
    "commercial": "商务标",
    "service": "服务",
    "qualification": "资格",
    "staffing": "人员",
    "performance": "业绩",
    "other": "其他",
    "unspecified": "其他",
}
_MISSING_RULE_DESCRIPTION_LENGTH_LIMIT = 12


def _volume_label(volume: str | None) -> str:
    """卷标识 → 中文卷名(未知值原样回退, 不泄露内部英文键给最终用户)。"""
    key = (volume or "unspecified").strip().lower()
    return _VOLUME_LABELS.get(key, volume or "其他")


def _gap_direction_phrase(actual: float, expected: float) -> str:
    """按偏差方向给出精确归因短语。

 职责: 高于基准提示"重复提取/识别错误"，低于基准提示"漏提"；避免机械统一用"漏提"
 描述超出场景，误导人工复核方向（超出 100 分不可能是"漏"）。
 不做: 不判断具体是哪一项重复/遗漏（人工复核职责）。
 """
    if actual > expected:
        return "超出基准，疑似评分项重复提取或分值识别错误"
    return "低于基准，疑似评分项漏提或分值识别错误"


def _format_volume_gap_detail(volume: str, check: dict) -> str:
    """单卷满分缺口明细文案：中文卷名 + 数值 + 方向性归因。"""
    label = _volume_label(volume)
    direction = _gap_direction_phrase(check["sum"], check["expected"])
    return f"{label}满分合计{check['sum']}≠基准{check['expected']}（{direction}）"


@dataclass(frozen=True)
class ScoreTotalsReport:
    """评分合计校验结果。"""

    total: float
    by_volume: dict[str, float]
    expected_total: float
    within_tolerance: bool
    needs_review: bool
    note: str
    item_count: int = 0
    missing_score_count: int = 0
    # 第3层: 按卷校验明细 {volume: {sum, expected, weight, within}} 与权重和是否=100%
    volume_checks: dict[str, dict] = field(default_factory=dict)
    weight_sum_ok: bool = True


def verify_score_totals(
    items: Iterable,
    *,
    volume_defs: dict[str, Any] | None = None,
    expected_total: float = 100.0,
    tolerance: float = 1.0,
) -> ScoreTotalsReport:
    """校验评分项满分合计。

    Args:
        items: 评分项序列，元素需有 score_weight(float) 与 volume(str|None) 属性。
        volume_defs: 卷定义 {volume: 含 weight/volume_max 属性的对象}; 提供则走按卷校验。
        expected_total: 全局口径期望满分合计（评标办法常为 100）。
        tolerance: 容差（四舍五入/小数误差）。

    Returns:
        ScoreTotalsReport: 合计、按卷分布、是否在容差内、是否需人工复核、按卷校验明细。
    """
    item_list = list(items)
    by_volume: dict[str, float] = {}
    total = 0.0
    missing = 0
    for it in item_list:
        weight = float(getattr(it, "score_weight", 0) or 0)
        # 阶段3: penalty/bonus 调整项不进入基础满分, 零分值是其正常形态, 不计缺失
        fact_type = str(getattr(it, "fact_type", "") or "base_score")
        if weight <= 0 and fact_type in ("base_score", "price_formula"):
            missing += 1
        total += weight
        volume = getattr(it, "volume", None) or "unspecified"
        by_volume[volume] = round(by_volume.get(volume, 0.0) + weight, 4)
    total = round(total, 4)

    if not item_list:
        _trace("TOTALS/empty-items")
        return ScoreTotalsReport(
            total=0.0,
            by_volume={},
            expected_total=expected_total,
            within_tolerance=False,
            needs_review=True,
            note="未提取到评分项",
            item_count=0,
            missing_score_count=0,
        )

    if volume_defs:
        _trace("TOTALS/by-volume-defs")
        return _build_by_volume_report(
            by_volume=by_volume,
            total=total,
            missing=missing,
            item_count=len(item_list),
            volume_defs=volume_defs,
            tolerance=tolerance,
        )

    independent_volume_report = _build_independent_100_volume_report(
        by_volume=by_volume,
        total=total,
        missing=missing,
        item_count=len(item_list),
        expected_total=expected_total,
        tolerance=tolerance,
    )
    if independent_volume_report is not None:
        _trace("TOTALS/independent-100-volumes")
        return independent_volume_report

    # 全局口径（无卷定义，单一100分制）
    _trace("TOTALS/global-single-scale")
    within = abs(total - expected_total) <= tolerance
    needs_review = (not within) or missing > 0
    if not within and missing > 0:
        note = (
            f"满分合计 {total} 与期望 {expected_total} 偏差超容差，且有 {missing} 项分值缺失，"
            f"{_gap_direction_phrase(total, expected_total)}，请人工复核评分表"
        )
    elif not within:
        note = (
            f"满分合计 {total} 与期望 {expected_total} 偏差超容差，"
            f"{_gap_direction_phrase(total, expected_total)}，请人工复核"
        )
    elif missing > 0:
        note = f"有 {missing} 项分值缺失(score_weight<=0)，请人工复核对应评分项"
    else:
        note = f"满分合计 {total} 与期望一致，校验通过"

    return ScoreTotalsReport(
        total=total,
        by_volume=by_volume,
        expected_total=expected_total,
        within_tolerance=within,
        needs_review=needs_review,
        note=note,
        item_count=len(item_list),
        missing_score_count=missing,
    )


def _build_independent_100_volume_report(
    *,
    by_volume: dict[str, float],
    total: float,
    missing: int,
    item_count: int,
    expected_total: float,
    tolerance: float,
) -> ScoreTotalsReport | None:
    """识别无权重声明但每个明确卷均独立闭合的百分制评分方案。"""
    if (
        missing > 0
        or len(by_volume) < 2
        or "unspecified" in by_volume
        or abs(expected_total - 100.0) > tolerance
    ):
        return None
    volume_checks = {
        volume: {
            "sum": volume_sum,
            "expected": 100.0,
            "weight": 0.0,
            "within": abs(volume_sum - 100.0) <= tolerance,
        }
        for volume, volume_sum in by_volume.items()
    }
    if not all(check["within"] for check in volume_checks.values()):
        return None
    return ScoreTotalsReport(
        total=total,
        by_volume=by_volume,
        expected_total=round(100.0 * len(by_volume), 4),
        within_tolerance=True,
        needs_review=False,
        note=f"{len(by_volume)}个独立百分制卷均完成100分满分校验",
        item_count=item_count,
        missing_score_count=0,
        volume_checks=volume_checks,
        weight_sum_ok=True,
    )


def _build_by_volume_report(
    *,
    by_volume: dict[str, float],
    total: float,
    missing: int,
    item_count: int,
    volume_defs: dict[str, Any],
    tolerance: float,
) -> ScoreTotalsReport:
    """按卷校验：每卷子项满分和应=卷满分(volume_max)，卷权重和应≈100%。

 双基准判定: "权重N%"型卷定义无"总分N分"文字证据时, parser 以 100 兜底
 volume_max; 但"最终得分制"文档(子项分值即最终得分, 卷合计=权重×100, 如技术60+商务40)
 会被 100 基准误判缺口。仅当全局合计≈100(平铺制强证据)时, 额外接受
 |卷合计 - weight×100| ≤ 容差; 折算制(各卷合计=卷基准, 全局合计≠100)不受影响,
 真缺口两个基准均不匹配仍被标记。
 """
    volume_checks: dict[str, dict] = {}
    off_volumes: list[str] = []
    flat_total_ok = abs(total - 100.0) <= tolerance
    for vol, vdef in volume_defs.items():
        vol_sum = round(by_volume.get(vol, 0.0), 4)
        expected = float(getattr(vdef, "volume_max", 0.0) or 0.0)
        weight = float(getattr(vdef, "weight", 0.0) or 0.0)
        within_folded = expected > 0 and abs(vol_sum - expected) <= tolerance
        flat_expected = round(weight * 100.0, 4)
        within_flat = (
            not within_folded
            and flat_total_ok
            and flat_expected > 0
            and abs(vol_sum - flat_expected) <= tolerance
        )
        within_vol = within_folded or within_flat
        volume_checks[vol] = {
            "sum": vol_sum,
            # 最终得分制命中时按其基准展示, 避免 UI/日志出现"40/100"误导
            "expected": flat_expected if within_flat else expected,
            "weight": weight,
            "within": within_vol,
        }
        if not within_vol:
            off_volumes.append(vol)

    weight_total = round(sum(c["weight"] for c in volume_checks.values()), 4)
    weight_sum_ok = abs(weight_total - 1.0) <= 0.02
    any_off = bool(off_volumes)
    needs_review = any_off or (not weight_sum_ok) or missing > 0

    parts: list[str] = []
    if off_volumes:
        detail = "；".join(
            _format_volume_gap_detail(v, volume_checks[v]) for v in off_volumes
        )
        parts.append(f"按卷满分校验未通过({detail})")
    if not weight_sum_ok:
        parts.append(f"卷权重合计{round(weight_total * 100, 1)}%≠100%，请核对各卷权重")
    if missing > 0:
        parts.append(f"有 {missing} 项分值缺失(score_weight<=0)")
    note = "；".join(parts) if parts else "按卷满分与权重校验通过"

    return ScoreTotalsReport(
        total=total,
        by_volume=by_volume,
        expected_total=100.0,
        within_tolerance=not any_off,
        needs_review=needs_review,
        note=note,
        item_count=item_count,
        missing_score_count=missing,
        volume_checks=volume_checks,
        weight_sum_ok=weight_sum_ok,
    )


@dataclass(frozen=True)
class ScoringTotalsEntry:
    """确认页/核计横幅评分项校验入参（领域无关适配对象）。

    职责: 把 Requirement/WritingClause 归一为满分校验所需的最小字段集。
    不做: 不承载 ORM 实体、不携带页面展示模型；diagnostic_name 仅在拒收时定位原项。
    """

    score_weight: float
    volume: Optional[str] = None
    volume_label: Optional[str] = None
    lot: Optional[str] = None
    scheme: Optional[str] = None
    volume_weight: Optional[float] = None
    volume_max: Optional[float] = None
    source_page: Optional[int] = None
    has_rules: bool = True
    description_length: Optional[int] = None
    is_llm_source: bool = False
    diagnostic_name: Optional[str] = None


def build_totals_report_payload(
    entries: list[ScoringTotalsEntry],
    *,
    extra_diagnostics: list[str] | None = None,
) -> Optional[dict[str, Any]]:
    """从评分项条目构建"按卷满分校验"报告 payload（审查确认页 + 写标核计横幅通用）。

 职责: 收口"重建卷定义 → verify_score_totals → needs_review 修正
 → note/diagnostics 组装"的确定性逻辑，两侧共用同一口径避免漂移。
 不做: 不读取 ORM 实体(调用方负责适配 ScoringTotalsEntry)、不做评分全集筛选。

 返回 None 表示无评分项(调用方据此不渲染横幅)。
 """
    if not entries:
        return None
    structure_review_required = diagnostics_require_review(extra_diagnostics)
    scheme_keys = sorted({e.scheme for e in entries if e.scheme})
    if len(scheme_keys) >= 2:
        # 多套评分方案按 scheme 分组独立核计, 禁止跨方案相加(新模块防堆叠)
        from app.core.extractor.scoring.scoring_scheme_totals import (
            build_scheme_totals_report_payload,
        )
        from app.core.extractor.scoring.volume_closure import (
            build_volume_closure_payload,
        )

        payload = build_scheme_totals_report_payload(
            entries,
            scheme_keys,
            extra_diagnostics=extra_diagnostics,
            structure_review_required=structure_review_required,
        )
        # 3484: 多方案闭合强证据(逐方案独立闭合)——verdict 判绿的免噪依据
        payload["volume_closure"] = build_volume_closure_payload(entries)
        return payload
    lot_keys = sorted({e.lot for e in entries if e.lot})
    if len(lot_keys) >= 2:
        return _build_lot_totals_report_payload(
            entries,
            lot_keys,
            extra_diagnostics=extra_diagnostics,
            structure_review_required=structure_review_required,
        )

    missing_rule_count = _count_missing_rule_entries(entries)
    authority_count, llm_count = _source_counts(entries)
    volume_defs: dict[str, Any] = {}
    adapted: list[Any] = []
    for e in entries:
        vol = e.volume
        adapted.append(SimpleNamespace(score_weight=float(e.score_weight or 0), volume=vol))
        if (
            vol
            and e.volume_weight is not None
            and e.volume_max is not None
            and vol not in volume_defs
        ):
            try:
                volume_defs[vol] = SimpleNamespace(
                    weight=float(e.volume_weight), volume_max=float(e.volume_max)
                )
            except (TypeError, ValueError):
                continue

    report = verify_score_totals(adapted, volume_defs=(volume_defs or None))
    # 展示构成按条目级分组标签细分(同一粗卷可含多个用户可见分组, 如
    # technical 卷下"技术建议书/主要人员/业绩"); 卷基准校验(volume_checks)仍按
    # 粗卷口径, 仅当该卷标签唯一时才用原文标签改名, 禁止首标签冒名整卷。
    by_volume_display = _display_by_volume(entries)
    volume_checks_display = _relabel_volume_mapping(
        report.volume_checks, _unique_volume_labels(entries)
    )

    # 确认页关注"已提取评分项满分是否补齐"，非"评标总权重是否覆盖所有卷"。
    # 价格卷通常按报价公式计分无评分子项，仅技术卷有子项且=100/100 时不应因 weight_sum_ok=False 误报。
    volume_checks = report.volume_checks
    has_volume_gap = any(not check.get("within", False) for check in volume_checks.values())
    if volume_checks:
        needs_review = (
            has_volume_gap
            or report.missing_score_count > 0
            or missing_rule_count > 0
        )
    else:
        # 无卷定义(单一100分制)时 volume_checks 为空，回退全局满分校验，
        # 弥补按卷校验对"整表级漏项/多计"的盲区(如技术65+价格30=95 误判通过)。
        needs_review = (
            (not report.within_tolerance)
            or report.missing_score_count > 0
            or missing_rule_count > 0
        )

    note = report.note
    if not needs_review and volume_checks:
        note = "按卷评分项满分校验通过"
    if (
        missing_rule_count > 0
        and not has_volume_gap
        and report.missing_score_count == 0
        and (volume_checks or report.within_tolerance)
    ):
        note = f"有 {missing_rule_count} 项评分项评审标准疑似缺失，请人工复核"

    diagnostics: list[str] = []
    for vol, check in volume_checks.items():
        if not check.get("within", False):
            diagnostics.append(_format_volume_gap_detail(vol, check))
    if report.missing_score_count > 0:
        diagnostics.append(f"有 {report.missing_score_count} 项评分项分值缺失")
    if missing_rule_count > 0:
        names = _missing_rule_diagnostic_names(entries)
        suffix = f"：{'、'.join(names)}" if names else ""
        diagnostics.append(
            f"有 {missing_rule_count} 项评分项评审标准疑似缺失，请人工补齐{suffix}"
        )
    if needs_review and not diagnostics and note:
        diagnostics.append(note)
    # 卷定义未覆盖全部有分卷时(如仅商务技术有基准、价格按公式无定义),
    # 单卷"已识别X/应为Y"缺全局上下文易被误读为"满分是Y", 补全局合计行。
    if needs_review and volume_checks:
        uncovered = [v for v in report.by_volume if v not in volume_checks]
        if uncovered:
            diagnostics.append(
                f"全局满分合计 {report.total} 分（{_volume_label(uncovered[0]) if len(uncovered) == 1 else '部分卷'}等未配置卷基准，仅作参考）"
            )

    # 结构丢失信号强制门禁（数值校验已通过但装配层有结构告警时仍需人工核对）
    if not needs_review and structure_review_required:
        _trace("TOTALS/structure-signal-forces-review")
        needs_review = True
        note = "评分表部分内容未能完整验收，请对照原文核对"

    if needs_review:
        composition = _format_scoring_composition(entries, report.by_volume)
        if composition:
            diagnostics.append(composition)
        diagnostics = _merge_extra_diagnostics(diagnostics, extra_diagnostics)

    from app.core.extractor.scoring.volume_closure import build_volume_closure_payload

    return {
        "needs_review": needs_review,
        "note": note,
        "by_volume": by_volume_display,
        "by_lot": {},
        "volume_checks": volume_checks_display,
        "weight_sum_ok": report.weight_sum_ok,
        "item_count": report.item_count,
        "missing_score_count": report.missing_score_count,
        "missing_rule_count": missing_rule_count,
        "authority_count": authority_count,
        "llm_count": llm_count,
        "structure_review_required": structure_review_required,
        "diagnostics": diagnostics,
        # 2026-08-31 用户裁决: PDF闭合强证据(细项不重复+各卷闭合)——verdict 判绿
        # 的免噪依据; 多方案/多标段路径不产该字段(scoped 逻辑管辖)。
        "volume_closure": build_volume_closure_payload(entries),
    }


def _source_counts(entries: list[ScoringTotalsEntry]) -> tuple[int, int]:
    """统计权威确定性来源与 LLM 兜底来源数量。"""
    llm_count = sum(1 for entry in entries if entry.is_llm_source)
    return max(len(entries) - llm_count, 0), llm_count


def _unique_volume_labels(entries: list[ScoringTotalsEntry]) -> dict[str, str]:
    """粗卷 → 唯一原文标签(继承语义); 同卷多标签时不改名。"""
    labels_by_volume: dict[str, set[str]] = {}
    for entry in entries:
        volume = entry.volume or "unspecified"
        label = _clean_volume_label(str(entry.volume_label or "").strip())
        if label:
            labels_by_volume.setdefault(volume, set()).add(label)
    return {
        volume: next(iter(labels))
        for volume, labels in labels_by_volume.items()
        if len(labels) == 1
    }


def _display_bucket_key(
    entry: ScoringTotalsEntry, inherited: dict[str, str]
) -> str:
    """条目的展示分组键: 自带标签 > 同卷唯一标签继承 > 原始卷键(下游映射中文)。"""
    label = _clean_volume_label(str(entry.volume_label or "").strip())
    if label:
        return label
    volume = entry.volume or "unspecified"
    return inherited.get(volume) or volume


def _display_by_volume(entries: list[ScoringTotalsEntry]) -> dict[str, float]:
    """展示构成: 按条目级分组标签汇总满分。"""
    inherited = _unique_volume_labels(entries)
    buckets: dict[str, float] = {}
    for entry in entries:
        key = _display_bucket_key(entry, inherited)
        buckets[key] = round(buckets.get(key, 0.0) + float(entry.score_weight or 0), 4)
    return buckets


def _relabel_volume_mapping(mapping: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for volume, value in mapping.items():
        key = labels.get(volume) or volume
        if isinstance(value, (int, float)) and key in out:
            out[key] = round(float(out[key]) + float(value), 4)
        else:
            out[key] = value
    return out


def _merge_extra_diagnostics(
    diagnostics: list[str],
    extra_diagnostics: list[str] | None,
) -> list[str]:
    """合并解析层附加诊断, 保持顺序并去重。"""
    if not extra_diagnostics:
        return diagnostics
    return list(dict.fromkeys([
        *diagnostics,
        *(str(item).strip() for item in extra_diagnostics if str(item).strip()),
    ]))


def _is_missing_rule_entry(entry: ScoringTotalsEntry) -> bool:
    """条目是否为“有有效满分、无规则、描述过短”的规则缺口。"""
    try:
        score_weight = float(entry.score_weight or 0)
    except (TypeError, ValueError):
        score_weight = 0.0
    description_length = entry.description_length
    return (
        score_weight > 0
        and not entry.has_rules
        and description_length is not None
        and description_length <= _MISSING_RULE_DESCRIPTION_LENGTH_LIMIT
    )


def _count_missing_rule_entries(entries: list[ScoringTotalsEntry]) -> int:
    """统计有有效满分但缺少评审规则、且描述过短的评分项。"""
    return sum(1 for entry in entries if _is_missing_rule_entry(entry))


def _missing_rule_diagnostic_names(entries: list[ScoringTotalsEntry]) -> list[str]:
    """按原顺序返回规则缺口项名，限制数量防止诊断文本膨胀。"""
    names = [
        str(entry.diagnostic_name or "").strip()
        for entry in entries
        if _is_missing_rule_entry(entry) and str(entry.diagnostic_name or "").strip()
    ]
    return list(dict.fromkeys(names))[:5]


def _build_lot_totals_report_payload(
    entries: list[ScoringTotalsEntry],
    lot_keys: list[str],
    *,
    extra_diagnostics: list[str] | None = None,
    structure_review_required: bool = False,
) -> dict[str, Any]:
    """按采购包/标段独立核计评分满分（+ 未归属护栏/去重/概览）。

 三处硬化:
 - 未归属条目(lot=None)不再被分组静默丢弃——计入全集并单独提示, 上游丢 lot 时
 fail-visible 而非把缺口伪装成"对应包漏提"。
 - 子报告全局回退路径会把 note 复制进 diagnostics, 外层不再同时追加两者, 消除
 同一句告警重复展示。
 - needs_review 时首行输出各包核计概览(含通过包), 避免"通过的包不出现在横幅上"
 被误读为"该包没提取到"。
 """
    by_lot: dict[str, float] = {}
    diagnostics: list[str] = []
    lot_status: list[str] = []
    needs_review = False
    missing_total = 0
    missing_rule_total = 0
    item_count = 0
    by_volume_global: dict[str, float] = {}
    authority_count, llm_count = _source_counts(entries)

    for lot in lot_keys:
        group_entries = [e for e in entries if e.lot == lot]
        stripped = [
            ScoringTotalsEntry(
                score_weight=e.score_weight,
                volume=e.volume,
                volume_label=e.volume_label,
                volume_weight=e.volume_weight,
                volume_max=e.volume_max,
                source_page=e.source_page,
                has_rules=e.has_rules,
                description_length=e.description_length,
                is_llm_source=e.is_llm_source,
                diagnostic_name=e.diagnostic_name,
            )
            for e in group_entries
        ]
        payload = build_totals_report_payload(stripped)
        if payload is None:
            continue
        lot_total = round(sum(float(e.score_weight or 0) for e in group_entries), 4)
        by_lot[lot] = lot_total
        item_count += int(payload.get("item_count", 0) or 0)
        missing_total += int(payload.get("missing_score_count", 0) or 0)
        missing_rule_total += int(payload.get("missing_rule_count", 0) or 0)
        for volume, score in payload.get("by_volume", {}).items():
            by_volume_global[volume] = round(by_volume_global.get(volume, 0.0) + float(score or 0), 4)
        lot_failed = bool(payload.get("needs_review"))
        lot_status.append(f"{lot} {lot_total}/100.0（{'未通过' if lot_failed else '通过'}）")
        if lot_failed:
            needs_review = True
            # 全局回退路径下 diagnostics[0] 即 note 同文, 二者取其一; dict.fromkeys 保序去重。
            lines = [d for d in payload.get("diagnostics", []) if d]
            if not lines:
                lines = [payload.get("note") or "评分项满分校验未通过"]
            for line in dict.fromkeys(lines):
                diagnostics.append(f"{lot}: {line}")

    unassigned = [e for e in entries if not e.lot]
    if unassigned:
        _trace("TOTALS/unassigned-lot-entries")
        needs_review = True
        unassigned_missing_rules = _count_missing_rule_entries(unassigned)
        missing_rule_total += unassigned_missing_rules
        unassigned_total = round(sum(float(e.score_weight or 0) for e in unassigned), 4)
        item_count += len(unassigned)
        unassigned_by_volume: dict[str, float] = {}
        for e in unassigned:
            vol = e.volume or "unspecified"
            unassigned_by_volume[vol] = round(
                unassigned_by_volume.get(vol, 0.0) + float(e.score_weight or 0), 4
            )
            by_volume_global[vol] = round(
                by_volume_global.get(vol, 0.0) + float(e.score_weight or 0), 4
            )
        diagnostics.append(
            f"另有 {len(unassigned)} 项评分项（合计 {unassigned_total} 分）未识别采购包归属，"
            f"未计入任何采购包核计，请人工核对其所属包"
        )
        composition = _format_scoring_composition(unassigned, unassigned_by_volume)
        if composition:
            diagnostics.append(f"未归属项{composition}")
        if unassigned_missing_rules > 0:
            diagnostics.append(
                f"未归属项有 {unassigned_missing_rules} 项评分项评审标准疑似缺失，请人工补齐"
            )

    if needs_review and lot_status:
        diagnostics.insert(0, "各采购包核计：" + "；".join(lot_status))

    if not needs_review:
        note = "按采购包评分项满分校验通过（" + "；".join(
            f"{lot} {score}/100.0" for lot, score in by_lot.items()
        ) + "）"
    else:
        note = "按采购包评分项满分校验未通过"
        if not diagnostics:
            diagnostics.append(note)
        diagnostics = _merge_extra_diagnostics(diagnostics, extra_diagnostics)

    # 结构丢失信号强制门禁（各包数值均通过但装配层有结构告警时仍需人工核对）
    if not needs_review and structure_review_required:
        needs_review = True
        note = "评分表部分内容未能完整验收，请对照原文核对"
        diagnostics = _merge_extra_diagnostics(diagnostics, extra_diagnostics)

    return {
        "needs_review": needs_review,
        "note": note,
        "by_volume": by_volume_global,
        "by_lot": by_lot,
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


def _format_scoring_composition(
    entries: list[ScoringTotalsEntry], by_volume: dict[str, float]
) -> str:
    """needs_review 时输出按分组构成明细，辅助用户定位核对来源。

 分组键与 _display_by_volume 同源(条目级标签), 禁止按粗卷折叠后再改名。
 """
    if not by_volume:
        return ""
    inherited = _unique_volume_labels(entries)
    buckets: dict[str, float] = {}
    pages_by_bucket: dict[str, list[int]] = {}
    for entry in entries:
        key = _display_bucket_key(entry, inherited)
        buckets[key] = round(buckets.get(key, 0.0) + float(entry.score_weight or 0), 4)
        if entry.source_page is None:
            continue
        try:
            page = int(entry.source_page)
        except (TypeError, ValueError):
            continue
        if page > 0:
            pages_by_bucket.setdefault(key, []).append(page)
    parts: list[str] = []
    for key, score in sorted(buckets.items(), key=lambda item: _volume_label(item[0])):
        page_text = _format_page_range(pages_by_bucket.get(key, []))
        parts.append(f"{_volume_label(key)} {score} 分{page_text}")
    total = round(sum(float(score or 0) for score in buckets.values()), 4)
    return f"已识别构成（合计{total}分）：" + "、".join(parts)


def _clean_volume_label(label: str) -> str:
    """清洗卷原文标签中自带的括注或裸尾分值，避免展示矛盾的重复分值。"""
    if not label:
        return ""
    cleaned = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", "", label)
    cleaned = strip_scoring_name_decoration(cleaned)
    return re.sub(r"\s+", "", cleaned).strip()


def _format_page_range(pages: list[int]) -> str:
    if not pages:
        return ""
    unique = sorted(set(pages))
    if not unique:
        return ""
    if len(unique) == 1:
        return f"（第{unique[0]}页）"
    return f"（第{unique[0]}-{unique[-1]}页）"
