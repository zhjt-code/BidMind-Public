"""评分事实作用域校验。

职责: 按 scheme×lot scope 统一验证评分事实完整性，分类异常路由，暴露失败 scope 与确定性期望。
不做: 不修改评分事实、不访问数据库、不选择 LLM 上下文、不持久化任何状态。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from app.core.extractor.scoring.score_totals import _volume_label
from app.core.extractor.scoring.scoring_facts import ScoringFactSet, ScoringScopeKey
from app.core.extractor.scoring.scoring_scope_contracts import (
    ScopeIdentity,
    ScopeVolumeDelta,
    ScoringScopeExpectation,
    ScoringScopeValidation,
    ScoringValidationKind,
)

# 零分门禁: 以下 fact_type 的零权重视为评分缺失，余者（bonus/penalty）零权重合法。
_MISSING_SCORE_FACT_TYPES = frozenset({"base_score", "price_formula"})

# 卷权重和容差（与 score_totals._build_by_volume_report 对齐）
_WEIGHT_SUM_TOLERANCE = 0.02


def validate_scoring_fact_set(
    fact_set: ScoringFactSet,
    *,
    expected_per_lot: float | None = None,
    tolerance: float = 1.0,
    declared_volume_defs: dict[str, Any] | None = None,
    declared_scope_expectations: Mapping[ScopeIdentity, ScoringScopeExpectation] | None = None,
) -> ScoringScopeValidation:
    """按 scheme×lot scope 独立校验评分事实完整性，暴露失败 scope 与确定性期望。

 expected_per_lot: None 表示兼容默认 100 分（不写入 expected_by_scope）；
 显式传值（如 120.0）表示外部权威基准，写入 expected_by_scope。
 declared_volume_defs: 文档声明的卷定义——声明卷基准但无事实必须 fail-visible。
 declared_scope_expectations: 外部权威声明的 scope 期望；其 scope 即使零事实也 fail-visible；
 内容写入 expected_by_scope 供 L3 筛选。
 """
    _compat_expected = expected_per_lot if expected_per_lot is not None else 100.0

    facts = fact_set.effective_facts

    scope_expectations: dict[ScopeIdentity, ScoringScopeExpectation] = {}
    if declared_scope_expectations:
        scope_expectations.update(declared_scope_expectations)

    if not facts:
        declared_failed = _sorted_scopes(scope_expectations.keys())
        return ScoringScopeValidation(
            kind=ScoringValidationKind.EVIDENCE_GAP,
            needs_review=True,
            note="未提取到评分事实",
            total=0.0,
            failed_scopes=declared_failed,
            expected_by_scope=MappingProxyType(dict(scope_expectations)),
        )

    empty_declared = _declared_volumes_without_facts(facts, declared_volume_defs)
    if empty_declared:
        detail = "；".join(
            f"{_volume_label(vol)}(卷基准{basis:g}分)" for vol, basis in empty_declared
        )
        return ScoringScopeValidation(
            kind=ScoringValidationKind.EVIDENCE_GAP,
            needs_review=True,
            note=f"已声明卷基准但未提取到该卷评分明细: {detail}，明细可能在其他章节/文件中",
            total=fact_set.total_score,
            totals_by_lot=fact_set.totals_by_lot(),
            failed_scopes=(),
            expected_by_scope=MappingProxyType(dict(scope_expectations)),
            scope_deltas=_build_scope_deltas(
                facts,
                {(f.scope.scheme, f.scope.lot) for f in facts},
                scope_expectations,
                declared_volume_defs,
                _compat_expected,
                tolerance,
            ),
        )

    totals_by_lot = fact_set.totals_by_lot()
    if ScoringScopeKey.UNASSIGNED_LOT in totals_by_lot:
        return ScoringScopeValidation(
            kind=ScoringValidationKind.SCOPE_CONFLICT,
            needs_review=True,
            note="存在未识别采购包归属的评分事实",
            total=fact_set.total_score,
            totals_by_lot=totals_by_lot,
            failed_scopes=(),
            expected_by_scope=MappingProxyType(dict(scope_expectations)),
        )

    totals_by_scope = fact_set.totals_by_scheme_lot()
    scheme_count = len({scheme for scheme, _ in totals_by_scope})

    all_scopes: set[ScopeIdentity] = set(totals_by_scope.keys())
    if declared_scope_expectations:
        all_scopes.update(declared_scope_expectations.keys())

    off_scopes: list[ScopeIdentity] = []
    for scope in all_scopes:
        scope_exp_obj = scope_expectations.get(scope)
        scope_expected = _scope_expected_total(scope_exp_obj, _compat_expected)
        if scope not in totals_by_scope:
            off_scopes.append(scope)
        elif not _scope_is_complete(facts, scope[0], scope[1], scope_expected, tolerance,
                                    scope_expectation=scope_exp_obj):
            off_scopes.append(scope)

    if expected_per_lot is not None:
        for scope in all_scopes:
            if scope not in scope_expectations:
                scope_expectations[scope] = ScoringScopeExpectation(
                    expected_total=expected_per_lot
                )

    failed_scopes_tuple = _sorted_scopes(off_scopes)

    if off_scopes:
        detail = "；".join(
            _scope_gap_detail(scope, totals_by_scope, scheme_count, _compat_expected,
                              scope_expectations)
            for scope in failed_scopes_tuple
        )
        return ScoringScopeValidation(
            kind=ScoringValidationKind.EVIDENCE_GAP,
            needs_review=True,
            note=f"评分事实核计未闭环: {detail}",
            total=fact_set.total_score,
            totals_by_lot=totals_by_lot,
            failed_scopes=failed_scopes_tuple,
            expected_by_scope=MappingProxyType(dict(scope_expectations)),
            scope_deltas=_build_scope_deltas(
                facts,
                set(failed_scopes_tuple),
                scope_expectations,
                declared_volume_defs,
                _compat_expected,
                tolerance,
            ),
        )

    if len(totals_by_scope) > 1:
        note = "评分事实核计通过（" + "；".join(
            f"{_scope_display(scheme, lot, scheme_count)} "
            f"{total}/{_scope_display_expected(scope_expectations.get((scheme, lot)), _compat_expected)}"
            for (scheme, lot), total in sorted(totals_by_scope.items())
        ) + "）"
    else:
        only_total = next(iter(totals_by_scope.values()))
        note = f"评分事实满分合计 {only_total} 与期望一致，校验通过"

    return ScoringScopeValidation(
        kind=ScoringValidationKind.COMPLETE,
        needs_review=False,
        note=note,
        total=fact_set.total_score,
        totals_by_lot=totals_by_lot,
        failed_scopes=(),
        expected_by_scope=MappingProxyType(dict(scope_expectations)),
    )


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _sorted_scopes(scopes: Any) -> tuple[ScopeIdentity, ...]:
    return tuple(sorted(set(scopes)))


def _scope_expected_total(
    scope_exp_obj: ScoringScopeExpectation | None,
    compat_default: float,
) -> float:
    """返回 scope 的有效期望总分（用于校验）: 优先 expected_total，否则 compat 默认。"""
    if scope_exp_obj is not None and scope_exp_obj.expected_total is not None:
        return scope_exp_obj.expected_total
    return compat_default


def _scope_display_expected(
    scope_exp_obj: ScoringScopeExpectation | None,
    compat_default: float,
) -> float:
    """返回 note 中显示的期望分母: expected_total > expected_by_volume 求和 > compat。"""
    if scope_exp_obj is not None:
        if scope_exp_obj.expected_total is not None:
            return scope_exp_obj.expected_total
        if scope_exp_obj.expected_by_volume:
            return round(sum(v for _, v in scope_exp_obj.expected_by_volume), 4)
    return compat_default


def _scope_gap_detail(
    scope: ScopeIdentity,
    totals_by_scope: dict[ScopeIdentity, float],
    scheme_count: int,
    compat_expected: float,
    scope_expectations: dict[ScopeIdentity, ScoringScopeExpectation],
) -> str:
    scheme, lot = scope
    label = _scope_display(scheme, lot, scheme_count)
    actual = totals_by_scope.get(scope, 0.0)
    exp_obj = scope_expectations.get(scope)
    expected = _scope_display_expected(exp_obj, compat_expected)
    return f"{label} {actual}/{expected}"


def _scope_display(scheme: str, lot: str, scheme_count: int) -> str:
    from app.core.extractor.scoring.scheme_segmentation import scheme_display_label

    parts: list[str] = []
    scheme_label = scheme_display_label(scheme, total_schemes=scheme_count)
    if scheme_label:
        parts.append(scheme_label)
    if lot != ScoringScopeKey.SINGLE_LOT:
        parts.append(lot)
    return "·".join(parts) if parts else "全集"


def _scope_is_complete(
    facts: list,
    scheme: str,
    lot: str,
    expected_per_lot: float,
    tolerance: float,
    *,
    scope_expectation: ScoringScopeExpectation | None = None,
) -> bool:
    """判断指定 scope 的评分事实是否闭合。

 校验优先级:
 1. scope_expectation.expected_by_volume（禁止退化 fallback100；实际 volume 集合须 ⊆ 声明集合）
 2. volume_defs（来自 fact.volume_max；冲突值 fail-visible）
 2.5 独立百分制识别（, 与 score_totals 报告层对称）
 3. expected_per_lot 全局总和（fallback）

 附加门禁:
 - base_score/price_formula 零权重 = 评分缺失
 - 卷权重声明: 有任一权重时全部 volume 须有权重；相同 volume 冲突权重 fail-visible
 - 权重和须 ≈1（容差 _WEIGHT_SUM_TOLERANCE）
 """
    scope_facts = [
        f for f in facts if f.scope.scheme == scheme and f.scope.lot == lot
    ]

    # 旧门禁: base_score/price_formula 零权重 = 评分缺失
    for fact in scope_facts:
        ft = getattr(fact, "fact_type", "base_score") or "base_score"
        if ft in _MISSING_SCORE_FACT_TYPES and fact.score_weight <= 0.0:
            return False

    by_volume: dict[str, float] = {}
    volume_defs: dict[str, float] = {}
    volume_weights: dict[str, float] = {}
    conflict_max = False
    conflict_weight = False

    for fact in scope_facts:
        vol = fact.scope.volume
        by_volume[vol] = round(by_volume.get(vol, 0.0) + float(fact.score_weight or 0), 4)

        # volume_max: 检测冲突（同 volume 不同 max 值）
        if fact.volume_max is not None:
            fvm = float(fact.volume_max)
            if vol in volume_defs:
                if abs(volume_defs[vol] - fvm) > 0.001:
                    conflict_max = True
            else:
                volume_defs[vol] = fvm

        # volume_weight: 检测冲突（同 volume 不同 weight）
        vw = getattr(fact, "volume_weight", None)
        if vw is not None:
            fvw = float(vw)
            if vol in volume_weights:
                if abs(volume_weights[vol] - fvw) > 0.001:
                    conflict_weight = True
            else:
                volume_weights[vol] = fvw

    if conflict_max or conflict_weight:
        return False

    # 卷权重和门禁：只有全部 volume 均已声明 volume_weight 时才校验 sum≈1。
    # 若仅部分 volume 有权重（partial），跳过此门禁，降级到 volume_max/总分逻辑，
    # 防止合法的平铺 100 分结构因个别卷权重缺失而误报 EVIDENCE_GAP。
    if volume_weights and set(volume_weights) == set(by_volume):
        weight_sum = round(sum(volume_weights.values()), 4)
        if abs(weight_sum - 1.0) > _WEIGHT_SUM_TOLERANCE:
            return False

    # 优先级 1: 显式 expected_by_volume（额外 volume 不得静默通过）
    if scope_expectation is not None and scope_expectation.expected_by_volume:
        evb = dict(scope_expectation.expected_by_volume)
        if not set(by_volume).issubset(set(evb)):
            return False
        return all(
            abs(by_volume.get(vol, 0.0) - exp_val) <= tolerance
            for vol, exp_val in evb.items()
        )

    # 优先级 2: volume_defs from fact.volume_max
    if volume_defs and set(by_volume).issubset(set(volume_defs)):
        return all(
            abs(by_volume.get(vol, 0.0) - exp_val) <= tolerance
            for vol, exp_val in volume_defs.items()
        )

    # 优先级 2.5: 独立百分制识别（, 与 score_totals._build_independent_100_volume_report
    # 判定条件严格对称）——无卷基准、期望≈100 且 ≥2 个明确卷各自闭合 100 时判定闭环。
    # 多卷各 100 的并行百分制文档全局合计必然 >100，不能落到优先级 3 全局 fallback 误判缺口，
    # 否则报告层已判通过的文档仍会触发 L3 空转（scope 级 180s 预算全额浪费）。
    # 卷权重不阻断捷径（与报告层对称, 遗留事项③）: 加权百分制"各卷100×权重和1"本身即闭合;
    # 全卷权重和≠1 已在前置权重和门禁 fail-visible，不会走到这里。
    if (
        not volume_defs
        and abs(expected_per_lot - 100.0) <= tolerance
        and len(by_volume) >= 2
        and ScoringScopeKey.UNSPECIFIED_VOLUME not in by_volume
        and all(abs(vol_sum - 100.0) <= tolerance for vol_sum in by_volume.values())
    ):
        return True

    # 优先级 3: 全局总和 fallback
    scope_total = round(sum(float(f.score_weight or 0) for f in scope_facts), 4)
    return abs(scope_total - expected_per_lot) <= tolerance


def _declared_volumes_without_facts(
    facts: list,
    declared_volume_defs: dict[str, Any] | None,
) -> list[tuple[str, float]]:
    from app.core.extractor.scoring.volume_parser import declared_volumes_without_coverage

    covered = {fact.scope.volume for fact in facts}
    return declared_volumes_without_coverage(covered, declared_volume_defs)


def _declared_volume_bases(
    declared_volume_defs: dict[str, Any] | None,
) -> dict[str, float]:
    """提取声明卷基准 volume→volume_max(>0)。"""
    bases: dict[str, float] = {}
    for vol, vdef in (declared_volume_defs or {}).items():
        try:
            basis = float(getattr(vdef, "volume_max", 0) or 0)
        except (TypeError, ValueError):
            continue
        if basis > 0:
            bases[vol] = basis
    return bases


def _build_scope_deltas(
    facts: list,
    scopes: set[ScopeIdentity],
    scope_expectations: dict[ScopeIdentity, ScoringScopeExpectation],
    declared_volume_defs: dict[str, Any] | None,
    compat_expected: float,
    tolerance: float,
) -> tuple[ScopeVolumeDelta, ...]:
    """逐卷双向对账明细(阶段3): 缺口与盈余同时暴露, 供重判反馈/救援路由/人工警告。

    卷基准优先级: scope_expectation.expected_by_volume > declared_volume_defs;
    无卷基准的卷 expected=None(方向 unknown); scope 总集恒附一条(volume="")。
    """
    declared_bases = _declared_volume_bases(declared_volume_defs)
    deltas: list[ScopeVolumeDelta] = []
    for scope in sorted(scopes):
        scheme, lot = scope
        by_volume: dict[str, float] = {}
        for fact in facts:
            if fact.scope.scheme != scheme or fact.scope.lot != lot:
                continue
            vol = fact.scope.volume
            by_volume[vol] = round(
                by_volume.get(vol, 0.0) + float(fact.score_weight or 0), 4
            )
        exp_obj = scope_expectations.get(scope)
        if exp_obj is not None and exp_obj.expected_by_volume:
            expected_map: dict[str, float] = dict(exp_obj.expected_by_volume)
        else:
            expected_map = dict(declared_bases)
        for vol in sorted(set(by_volume) | set(expected_map)):
            deltas.append(
                ScopeVolumeDelta(
                    scope=scope,
                    volume=vol,
                    actual=by_volume.get(vol, 0.0),
                    expected=expected_map.get(vol),
                    tolerance=tolerance,
                )
            )
        scope_expected: float | None
        if exp_obj is not None and exp_obj.expected_total is not None:
            scope_expected = exp_obj.expected_total
        elif expected_map:
            scope_expected = round(sum(expected_map.values()), 4)
        else:
            scope_expected = compat_expected
        deltas.append(
            ScopeVolumeDelta(
                scope=scope,
                volume="",
                actual=round(sum(by_volume.values()), 4),
                expected=scope_expected,
                tolerance=tolerance,
            )
        )
    return tuple(deltas)


__all__ = [
    "ScopeVolumeDelta",
    "ScoringScopeExpectation",
    "ScoringScopeValidation",
    "ScopeIdentity",
    "ScoringValidationKind",
    "validate_scoring_fact_set",
]
