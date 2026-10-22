"""
职责: 把 pair 级情形账本挂到对比结果, 并在一级情形不可核查时阻断"无风险"结论。
不做: 规则命中判定、维度评分、风险等级推导。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from loguru import logger

from app.core.collusion.rules.facts import build_document_facts
from app.core.collusion.rules.ledger import (
    build_pair_scenario_ledger,
    summarize_ledger_gate,
)
from app.core.collusion.rules.registry import load_rule_registry
from app.models.document import Document

# 面向用户的缺失字段最多列举个数, 超出部分折叠为"等"
_MAX_LISTED_LABELS = 4

_GATE_RISK_EXPLANATION = (
    "本对关键身份信息缺失，属于可直接定性的串标情形无法核查，"
    "不能据此认定为无风险，建议补充材料后人工复核。"
)


def _build_gate_flag(missing_labels: Sequence[str]) -> str:
    """拼装用户可见的闸门说明; 只出现中文字段标签, 不得出现 rule_id 等内部标识。"""
    if not missing_labels:
        return "关键身份信息缺失，可直接定性的串标情形无法核查，暂不判定为无风险。"
    listed = "、".join(missing_labels[:_MAX_LISTED_LABELS])
    suffix = "等" if len(missing_labels) > _MAX_LISTED_LABELS else ""
    return (
        f"关键身份信息缺失（{listed}{suffix}），"
        "可直接定性的串标情形无法核查，暂不判定为无风险。"
    )


def _is_clean_verdict(compare_result: dict[str, Any]) -> bool:
    """判断当前结论是否为"已完成核查且无异常"。仅这种结论才允许被闸门降级。"""
    if compare_result.get("decision_state") != "decidable":
        return False
    if compare_result.get("risk_level") not in (None, "", "none"):
        return False
    overall = str(compare_result.get("overall_risk") or "").strip().lower()
    return overall in ("", "none")


def apply_ledger_gate(compare_result: dict[str, Any], gate: dict[str, Any]) -> bool:
    """闸门阻断时把"无异常"降级为待复核; 返回是否实际发生降级。

    只降级不升级: 已有正向证据或已是待复核/失败态的结论一律不动,
    保证情形账本不会压制真实命中, 也不会重复叠加说明。
    """
    if not gate.get("blocked") or not _is_clean_verdict(compare_result):
        return False

    flags = [str(x) for x in (compare_result.get("quality_flags") or []) if str(x).strip()]
    flag = _build_gate_flag(list(gate.get("missing_fact_labels") or []))
    if flag not in flags:
        flags.append(flag)

    compare_result["quality_flags"] = flags
    compare_result["decision_state"] = "needs_review"
    compare_result["review_state"] = "needs_review"
    compare_result["risk_level"] = None
    compare_result["overall_risk"] = "inconclusive"
    compare_result["risk_explanation"] = _GATE_RISK_EXPLANATION
    return True


def enforce_pair_scenario_ledger(
    compare_result: dict[str, Any],
    doc_a: Document,
    doc_b: Document,
    info_a: dict[str, Any] | None,
    info_b: dict[str, Any] | None,
) -> None:
    """构建本对情形账本并挂载, 一级情形不可核查时阻断"无风险"结论。

    账本写入 ``compare_result["scenario_ledger"]``, 随 llm_analysis 一并落库。
    任何异常都降级为"无账本"并告警, 不阻断主对比流程。
    """
    try:
        registry = load_rule_registry()
        facts_a = build_document_facts(doc_a.id, doc_a.filename or "", info_a)
        facts_b = build_document_facts(doc_b.id, doc_b.filename or "", info_b)
        ledger = build_pair_scenario_ledger(registry, facts_a, facts_b)
        gate = summarize_ledger_gate(ledger, registry)

        payload = ledger.to_dict()
        payload["gate"] = gate
        compare_result["scenario_ledger"] = payload

        if apply_ledger_gate(compare_result, gate):
            logger.info(
                "[Analyzer] 情形账本阻断无风险结论: pair=({},{}), 缺失字段={}",
                doc_a.id, doc_b.id, gate.get("missing_fact_labels"),
            )
    except Exception as exc:
        logger.warning(
            "[Analyzer] 情形账本构建降级: pair=({},{}), error={}",
            getattr(doc_a, "id", None), getattr(doc_b, "id", None), exc,
        )
