"""B4.6 algorithm-first risk band 推导器。

纯函数决策树：基于时效性/金额/相关性三维确定性输入，
产出候选 risk_band + confidence，供 AlgorithmicRiskBandGate 与 LLM 判定对比。

决策树优先级（高→低）:
1. is_mandatory AND (date out_of_range OR amount unparseable OR relevance < 30) → high/0.95
2. is_mandatory AND any dimension uncertain (date ambiguous/unparseable, amount low-confidence) → medium/0.6
3. relevance >= 80 AND other dims pass → none/0.9
4. 50 <= relevance < 80 → low/0.8
5. fallback → medium/0.3
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import logging

from app.core.extractor.tools.amount_parser import AmountVerdict
from app.core.extractor.tools.date_range_checker import DateRangeVerdict
from app.core.matcher.relevance_scorer import RelevanceScore

logger = logging.getLogger(__name__)

RiskBand = Literal["high", "medium", "low", "none"]


@dataclass(frozen=True)
class RiskBandVerdict:
    """算法推导的 risk_band 结果。

    Attributes:
        risk_band:           推导结果：high / medium / low / none。
        confidence:          本次推导的置信度 [0, 1]。
        contributing_factors: 触发当前规则的各维度实际值及触发原因。
        inputs_snapshot:     原始输入摘要，用于审计与回溯。
    """

    risk_band: RiskBand
    confidence: float
    contributing_factors: dict[str, Any] = field(default_factory=dict)
    inputs_snapshot: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _date_check(date_verdict: DateRangeVerdict | None) -> str | None:
    """提取 verdict 字符串，None 安全。"""
    return date_verdict.verdict if date_verdict is not None else None


def _amount_parseable(amount_verdict: AmountVerdict | None) -> bool:
    """amount 可解析（canonical_value 非 None）则为 True。"""
    if amount_verdict is None:
        return True  # 无输入 → 不视为错误
    return amount_verdict.canonical_value is not None


def _amount_low_confidence(amount_verdict: AmountVerdict | None) -> bool:
    """amount 置信度为 low（含不可解析）。"""
    if amount_verdict is None:
        return False
    return amount_verdict.confidence == "low"


def _relevance_score(relevance: RelevanceScore | None) -> int | None:
    return relevance.score if relevance is not None else None


def _build_snapshot(
    is_mandatory: bool,
    date_verdict: DateRangeVerdict | None,
    amount_verdict: AmountVerdict | None,
    relevance: RelevanceScore | None,
) -> dict[str, Any]:
    """构造 inputs_snapshot，记录所有输入的原始关键字段。"""
    snapshot: dict[str, Any] = {"is_mandatory": is_mandatory}

    if date_verdict is not None:
        snapshot["date_verdict"] = date_verdict.verdict
        snapshot["date_parsed_range"] = (
            (str(date_verdict.parsed_range[0]), str(date_verdict.parsed_range[1]))
            if date_verdict.parsed_range
            else None
        )
        snapshot["date_ambiguity_reasons"] = date_verdict.ambiguity_reasons
    else:
        snapshot["date_verdict"] = None

    if amount_verdict is not None:
        snapshot["amount_confidence"] = amount_verdict.confidence
        snapshot["amount_canonical_value"] = (
            str(amount_verdict.canonical_value)
            if amount_verdict.canonical_value is not None
            else None
        )
        snapshot["amount_unit"] = amount_verdict.unit
    else:
        snapshot["amount_verdict"] = None

    if relevance is not None:
        snapshot["relevance_score"] = relevance.score
        snapshot["relevance_type_match"] = relevance.type_match
        snapshot["relevance_semantic_sim"] = relevance.semantic_sim
    else:
        snapshot["relevance_score"] = None

    return snapshot


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def derive_risk_band(
    is_mandatory: bool,
    date_verdict: DateRangeVerdict | None,
    amount_verdict: AmountVerdict | None,
    relevance: RelevanceScore | None,
) -> RiskBandVerdict:
    """Derive risk band using decision tree (priority order).

    Rules (evaluated in order; first match wins):

    1. ``is_mandatory AND (date out_of_range OR amount unparseable OR relevance < 30)``
       → ``high`` / 0.95

       "amount unparseable" = ``AmountVerdict.canonical_value is None``
       "relevance < 30"     = ``RelevanceScore.score < 30``

    2. ``is_mandatory AND any dimension uncertain``
       → ``medium`` / 0.6

       "date uncertain"        = verdict in {"ambiguous", "unparseable"}
       "amount low-confidence" = ``AmountVerdict.confidence == "low"``
         (parseable but uncertain, e.g. Chinese numeral without unit)

    3. ``relevance >= 80 AND date passes AND amount passes``
       → ``none`` / 0.9

       "date passes"   = ``date_verdict is None`` or ``verdict == "in_range"``
       "amount passes" = ``amount_verdict is None`` or ``canonical_value is not None``

    4. ``50 <= relevance < 80``
       → ``low`` / 0.8

    5. Fallback
       → ``medium`` / 0.3

    Args:
        is_mandatory:  Whether the requirement is a knockout criterion.
        date_verdict:  Output of :func:`~app.core.extractor.tools.date_range_checker.check_performance_anchor`,
                       or ``None`` when the requirement has no time component.
        amount_verdict: Output of :func:`~app.core.extractor.tools.amount_parser.parse_amount`,
                        or ``None`` when amount is not applicable.
        relevance:     Output of :func:`~app.core.matcher.relevance_scorer.score_relevance`,
                       or ``None`` when relevance was not computed.

    Returns:
        :class:`RiskBandVerdict` with risk_band, confidence, contributing factors,
        and a full inputs snapshot for audit.
    """
    # 防御性降级: 历史上 reviewer._compute_algorithm_evidence 曾把
    # compare_amount() 的字符串结果塞入 amount_verdict, 下游访问 .confidence
    # 直接 AttributeError 打崩整条审查链路。这里对三类输入做类型兜底:
    # - 非预期类型 -> 视为 None + 记 degraded_inputs, 避免再次单点传错导致全量失败;
    # - 决策树业务语义不变: 降级后等同"维度不适用", 规则跳过而非误判。
    degraded: list[str] = []
    if date_verdict is not None and not isinstance(date_verdict, DateRangeVerdict):
        logger.warning(
            "derive_risk_band: date_verdict 类型异常, 已降级为 None: type=%s",
            type(date_verdict).__name__,
        )
        degraded.append(f"date_verdict({type(date_verdict).__name__})")
        date_verdict = None
    if amount_verdict is not None and not isinstance(amount_verdict, AmountVerdict):
        logger.warning(
            "derive_risk_band: amount_verdict 类型异常, 已降级为 None: type=%s",
            type(amount_verdict).__name__,
        )
        degraded.append(f"amount_verdict({type(amount_verdict).__name__})")
        amount_verdict = None
    if relevance is not None and not isinstance(relevance, RelevanceScore):
        logger.warning(
            "derive_risk_band: relevance 类型异常, 已降级为 None: type=%s",
            type(relevance).__name__,
        )
        degraded.append(f"relevance({type(relevance).__name__})")
        relevance = None

    inputs_snapshot = _build_snapshot(is_mandatory, date_verdict, amount_verdict, relevance)
    if degraded:
        inputs_snapshot["degraded_inputs"] = degraded

    dv = _date_check(date_verdict)
    rel_score = _relevance_score(relevance)
    amount_ok = _amount_parseable(amount_verdict)

    # ------------------------------------------------------------------
    # Rule 1: mandatory + critical failure in any dimension → high
    # ------------------------------------------------------------------
    date_out_of_range = dv == "out_of_range"
    amount_below = not amount_ok  # canonical_value is None
    rel_critical = rel_score is not None and rel_score < 30

    if is_mandatory and (date_out_of_range or amount_below or rel_critical):
        triggers = []
        if date_out_of_range:
            triggers.append("date_out_of_range")
        if amount_below:
            triggers.append("amount_unparseable")
        if rel_critical:
            triggers.append(f"relevance_critical({rel_score})")

        return RiskBandVerdict(
            risk_band="high",
            confidence=0.95,
            contributing_factors={
                "date_check": dv,
                "amount_check": (
                    "unparseable"
                    if amount_below
                    else (amount_verdict.confidence if amount_verdict else None)
                ),
                "relevance_check": rel_score,
                "triggers": triggers,
            },
            inputs_snapshot=inputs_snapshot,
        )

    # ------------------------------------------------------------------
    # Rule 2: mandatory + uncertain dimension → medium
    # ------------------------------------------------------------------
    date_uncertain = dv in ("ambiguous", "unparseable")
    amt_low_conf = _amount_low_confidence(amount_verdict)

    if is_mandatory and (date_uncertain or amt_low_conf):
        triggers = []
        if date_uncertain:
            triggers.append(f"date_uncertain({dv})")
        if amt_low_conf:
            triggers.append("amount_low_confidence")

        return RiskBandVerdict(
            risk_band="medium",
            confidence=0.6,
            contributing_factors={
                "date_check": dv,
                "amount_check": amount_verdict.confidence if amount_verdict else None,
                "relevance_check": rel_score,
                "triggers": triggers,
            },
            inputs_snapshot=inputs_snapshot,
        )

    # ------------------------------------------------------------------
    # Rule 3: high relevance + all dimensions pass → none
    # ------------------------------------------------------------------
    date_passes = date_verdict is None or dv == "in_range"
    amount_passes = amount_verdict is None or amount_ok

    if rel_score is not None and rel_score >= 80 and date_passes and amount_passes:
        return RiskBandVerdict(
            risk_band="none",
            confidence=0.9,
            contributing_factors={
                "date_check": dv,
                "amount_check": amount_verdict.confidence if amount_verdict else None,
                "relevance_check": rel_score,
                "triggers": ["high_relevance_all_pass"],
            },
            inputs_snapshot=inputs_snapshot,
        )

    # ------------------------------------------------------------------
    # Rule 4: medium relevance → low
    # ------------------------------------------------------------------
    if rel_score is not None and 50 <= rel_score < 80:
        return RiskBandVerdict(
            risk_band="low",
            confidence=0.8,
            contributing_factors={
                "date_check": dv,
                "amount_check": amount_verdict.confidence if amount_verdict else None,
                "relevance_check": rel_score,
                "triggers": [f"medium_relevance({rel_score})"],
            },
            inputs_snapshot=inputs_snapshot,
        )

    # ------------------------------------------------------------------
    # Rule 5: fallback
    # ------------------------------------------------------------------
    return RiskBandVerdict(
        risk_band="medium",
        confidence=0.3,
        contributing_factors={
            "date_check": dv,
            "amount_check": amount_verdict.confidence if amount_verdict else None,
            "relevance_check": rel_score,
            "triggers": ["fallback"],
        },
        inputs_snapshot=inputs_snapshot,
    )
