"""SchemeMap 判读基准推导。

职责: 从核验后 SchemeMap 推导缺失的卷基准(volume_defs)与 scope 期望
 (expected_by_volume), 供逐卷对账与 L2/L3 救援目标消费。
不做: 不贴标、不校验守恒、不调用 LLM、不覆盖任何确定性声明(只补缺)。
"""

from __future__ import annotations

from typing import Any

from app.core.extractor.scoring.scheme_reader.contracts import SchemeMap
from app.core.extractor.scoring.scoring_facts import ScoringScopeKey
from app.core.extractor.scoring.scoring_scope_contracts import (
    ScopeIdentity,
    ScoringScopeExpectation,
)

_EPS = 0.001


def derive_volume_defs_from_map(
    verified_map: SchemeMap | None,
    existing_volume_defs: dict[str, Any] | None,
    *,
    verified_volume_keys: set[tuple[int, str]] | None = None,
) -> dict[str, Any]:
    """从判读产物补齐缺失卷基准; 确定性声明恒优先, 同卷跨方案取值冲突时跳过该卷。

 verified_volume_keys 非 None 时只采纳锚点核验通过的卷声明(:
 prompt 示例分值可能被 LLM 抄进输出, 未锚定的声明是 claim 不是 fact)。
 权重按所属方案基准折算(total_score 缺失时用方案内卷基准和), 供报告层展示;
 对账闭合判定只消费 volume_max。
 """
    from app.core.extractor.scoring.volume_parser import VolumeDefinition

    if verified_map is None:
        return {}
    existing = existing_volume_defs or {}
    candidates: dict[str, tuple[float, str | None, float]] = {}
    conflicts: set[str] = set()
    for scheme in verified_map.schemes:
        vols = [
            v for v in scheme.volumes
            if v.max_score and float(v.max_score) > 0
            and (
                verified_volume_keys is None
                or (scheme.scheme_index, v.volume) in verified_volume_keys
            )
        ]
        denom = (
            float(scheme.total_score)
            if scheme.total_score and float(scheme.total_score) > 0
            else sum(float(v.max_score) for v in vols)
        )
        for vol in vols:
            max_score = float(vol.max_score)
            prior = candidates.get(vol.volume)
            if prior is not None and abs(prior[0] - max_score) > _EPS:
                conflicts.add(vol.volume)
                continue
            weight = round(max_score / denom, 4) if denom > 0 else 0.0
            candidates.setdefault(vol.volume, (max_score, vol.label, weight))

    derived: dict[str, Any] = {}
    for volume, (max_score, label, weight) in candidates.items():
        if volume in conflicts or volume in existing:
            continue
        derived[volume] = VolumeDefinition(
            volume=volume, weight=weight, volume_max=max_score, label=label or None
        )
    return derived


def derive_scope_expectations_from_map(
    verified_map: SchemeMap | None,
    *,
    verified_volume_keys: set[tuple[int, str]] | None = None,
) -> dict[ScopeIdentity, ScoringScopeExpectation]:
    """按方案生成 (scheme_N, __single__) 键的 expected_by_volume 期望。

 只输出 expected_by_volume, 不输出 expected_total——方案 total_score 常为加权
 折算值(如各卷21/49/30而方案总分100), 直接当原始分母会产生错误缺口。
 verified_volume_keys 非 None 时只采纳锚点核验通过的卷声明。
 """
    if verified_map is None:
        return {}
    expectations: dict[ScopeIdentity, ScoringScopeExpectation] = {}
    for scheme in verified_map.schemes:
        by_volume = tuple(
            sorted(
                (v.volume, float(v.max_score))
                for v in scheme.volumes
                if v.max_score and float(v.max_score) > 0
                and (
                    verified_volume_keys is None
                    or (scheme.scheme_index, v.volume) in verified_volume_keys
                )
            )
        )
        if not by_volume:
            continue
        scope: ScopeIdentity = (
            f"scheme_{scheme.scheme_index}",
            ScoringScopeKey.SINGLE_LOT,
        )
        try:
            expectations[scope] = ScoringScopeExpectation(expected_by_volume=by_volume)
        except ValueError:
            continue
    return expectations


def merge_judged_basis(
    *,
    context: Any,
    volume_defs: dict[str, Any],
    scope_expectations: dict | None,
) -> tuple[dict | None, bool, str]:
    """判读基准合并单入口(rescue tail 薄委托点)。

 就地补充 volume_defs 缺失卷; scope_expectations 仅在完全缺失时用判读期望兜底。
 返回 (生效的 scope_expectations, 是否有合并, 诊断文本)。判读不可用时零改动。

 准入门槛: 注入对账基准是高影响动作, 其准入不得低于贴标——
 有效置信度须达 SCORING_SCHEME_READER_MIN_CONFIDENCE, 且只采纳锚点核验通过的卷声明。
 """
    from app.core.config import settings

    if context is None or not getattr(context, "usable", False):
        return scope_expectations, False, ""
    min_confidence = float(
        getattr(settings, "SCORING_SCHEME_READER_MIN_CONFIDENCE", 0.6) or 0.6
    )
    if context.reading.effective_confidence < min_confidence:
        return scope_expectations, False, ""
    verified_map = context.reading.verified_map
    verified_keys = context.reading.verified_volume_keys

    added_defs = derive_volume_defs_from_map(
        verified_map, volume_defs, verified_volume_keys=verified_keys
    )
    volume_defs.update(added_defs)

    effective_expectations = scope_expectations
    added_expectations = 0
    if not scope_expectations:
        judged = derive_scope_expectations_from_map(
            verified_map, verified_volume_keys=verified_keys
        )
        if judged:
            effective_expectations = judged
            added_expectations = len(judged)

    if not added_defs and not added_expectations:
        return effective_expectations, False, ""
    parts: list[str] = []
    if added_defs:
        parts.append(
            "补卷基准" + ",".join(
                f"{v}={d.volume_max:g}" for v, d in sorted(added_defs.items())
            )
        )
    if added_expectations:
        parts.append(f"补方案期望{added_expectations}个")
    return effective_expectations, True, "评分结构判读基准: " + "; ".join(parts)


__all__ = [
    "derive_scope_expectations_from_map",
    "derive_volume_defs_from_map",
    "merge_judged_basis",
]
