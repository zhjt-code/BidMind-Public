"""PDF闭合强证据（2026-08-31 用户裁决: pdf_direct 判绿基准）。

职责: 从满分核计条目(ScoringTotalsEntry 形态, 鸭子类型)推导"细项不重复且
      分数闭合"的强证据载荷——全部细项归卷、各卷合计与其声明满分闭合、
      折算轴权重存在时和≈1、无 (名称,分值,卷) 三元重复。各卷 weight 均为 1.0
      视为卷内满分盖章, 不进入折算轴求和。多方案文档按 scheme
      分区独立评估, 全部方案各自闭合才判 verified(3484 教训: 方案轴是守恒
      方程的分母, 跨方案求和无意义)。
不做: 不读 chunk/DB、不判 verdict 颜色(scoring_green_verdict 职责)、不改条目、
      不处理多标段(≥2 lot 返回 None, scoped 逻辑管辖)。

证据边界(与用户裁决一致): 基准取条目上盖章的 volume_max——它来自 PDF 直读
锚定后的卷声明(或确定性链卷定义), 不依赖 chunk 声明捕获。已知残余风险:
文档无卷权重且模型整卷/整方案漏提并同步少报声明时, 本证据无法识别(重询
反馈与人工确认页兜底; 声明方案数与提取方案数的比对留待附件适用关系解析)。
"""

from __future__ import annotations

from typing import Any, Optional

_TOLERANCE = 0.01
_WEIGHT_SUM_TOLERANCE = 0.02


def _evaluate_partition(entries: list[Any]) -> dict[str, Any]:
    """单方案分区闭合评估(全部归卷+基准一致+各卷闭合+权重和探测)。"""

    def _fail(reason: str, groups: Optional[list] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"verified": False, "reason": reason}
        if groups:
            payload["groups"] = groups
        return payload

    seen: set[tuple[str, float, str]] = set()
    group_sums: dict[str, float] = {}
    group_max: dict[str, float] = {}
    group_weights: dict[str, Optional[float]] = {}
    for e in entries:
        weight = float(e.score_weight or 0)
        if weight <= 0:
            return _fail("missing_score")
        label = str(e.volume_label or "").strip() or str(e.volume or "").strip()
        if not label or e.volume_max is None:
            return _fail("unlabeled")
        identity = (str(e.diagnostic_name or "").strip(), weight, label)
        if identity[0] and identity in seen:
            return _fail("duplicate")
        seen.add(identity)
        expected = float(e.volume_max)
        if label in group_max and abs(group_max[label] - expected) > _TOLERANCE:
            return _fail("baseline_conflict")
        group_max[label] = expected
        group_sums[label] = group_sums.get(label, 0.0) + weight
        vw = e.volume_weight
        if label not in group_weights or group_weights[label] is None:
            group_weights[label] = float(vw) if vw is not None else None

    groups = []
    for label, extracted in group_sums.items():
        expected = group_max[label]
        gap = round(extracted - expected, 4)
        groups.append(
            {"label": label, "expected": expected,
             "extracted": round(extracted, 4), "gap": gap}
        )
        if abs(gap) > _TOLERANCE:
            return _fail("group_gap", groups)

    weights = _fold_axis_weights(group_weights)
    if weights:
        # 权重是"整卷漏提"的PDF原生探测器: 部分卷带折算份额而漏提的卷不在场时和≠1
        if len(weights) != len(group_sums):
            return _fail("weight_partial")
        if abs(sum(weights) - 1.0) > _WEIGHT_SUM_TOLERANCE:
            return _fail("weight_sum")

    return {"verified": True, "reason": None, "groups": groups}


def _fold_axis_weights(group_weights: dict[str, Optional[float]]) -> list[float]:
    """取出文档折算轴权重。每卷都是 1.0 时视同未声明折算轴。

    职责: 区分「本卷满分盖章」与「文档折算份额」。
    不做: 不改条目、不判 group_gap、不参与 verdict。
    """
    weights = [w for w in group_weights.values() if w is not None]
    if (
        weights
        and len(weights) == len(group_weights)
        and all(abs(w - 1.0) <= _WEIGHT_SUM_TOLERANCE for w in weights)
    ):
        return []
    return weights


def build_volume_closure_payload(entries: list[Any]) -> Optional[dict[str, Any]]:
    """条目 → PDF闭合强证据载荷; 多标段返回 None(不适用)。

    单方案: verified 当且仅当全部条目带分值且归卷、同卷基准一致、各卷合计
    与基准差 ≤0.01、折算轴权重存在时全覆盖且和≈1、无三元重复(basis=volume_max)。
    各卷 weight 均为 1.0 视为卷内满分盖章(并行百分制), 不进入折算轴求和。
    多方案(≥2 scheme): 按 scheme 分区, 每区独立按上述规则评估, 全部通过才
    verified(basis=per_scheme); 存在未归方案条目时 fail-closed。
    """
    if not entries:
        return None
    if len({e.lot for e in entries if e.lot}) >= 2:
        return None

    schemes = {str(e.scheme).strip() for e in entries if str(e.scheme or "").strip()}
    labeled_count = sum(1 for e in entries if str(e.scheme or "").strip())
    # 部分条目带方案、其余游离 → 分区不完整, fail-closed(不论方案数)
    if 0 < labeled_count < len(entries):
        return {"verified": False, "reason": "scheme_partial", "basis": "per_scheme"}
    if len(schemes) < 2:
        payload = _evaluate_partition(list(entries))
        payload["basis"] = "volume_max"
        return payload
    scheme_reports: dict[str, dict[str, Any]] = {}
    for scheme in sorted(schemes):
        partition = [e for e in entries if str(e.scheme or "").strip() == scheme]
        scheme_reports[scheme] = _evaluate_partition(partition)
    failed = {
        scheme: report["reason"]
        for scheme, report in scheme_reports.items()
        if not report["verified"]
    }
    if failed:
        return {
            "verified": False,
            "reason": "; ".join(f"{s}:{r}" for s, r in sorted(failed.items()))[:200],
            "basis": "per_scheme",
        }
    return {
        "verified": True,
        "reason": None,
        "basis": "per_scheme",
        "schemes": sorted(schemes),
        "groups": [
            {**group, "scheme": scheme}
            for scheme, report in sorted(scheme_reports.items())
            for group in report.get("groups", [])
        ],
    }


__all__ = ["build_volume_closure_payload"]
