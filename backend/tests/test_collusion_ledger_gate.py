"""情形账本闸门防御测试。

覆盖形态: 「一级串标情形因字段缺失无法核查」被误判为「无风险」。
同形态两条夹具(不同实体): 法定代表人缺失 / 人员证书与身份证尾号缺失。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.collusion.rules.pair_gate import (
    apply_ledger_gate,
    enforce_pair_scenario_ledger,
)

_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "collusion_rules" / "scenario_extractions.json"
)


@pytest.fixture(scope="module")
def scenarios() -> dict[str, Any]:
    with _FIXTURE_PATH.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _doc(doc_id: int, filename: str) -> Any:
    """构造最小 Document 替身，只提供 pair_gate 实际读取的两个属性。"""
    return SimpleNamespace(id=doc_id, filename=filename)


def _clean_verdict() -> dict[str, Any]:
    """_finalize_pair_contract 判定「已完成核查且无异常」时的结果形态。"""
    return {
        "decision_state": "decidable",
        "analysis_state": "completed",
        "review_state": "completed",
        "risk_level": "none",
        "overall_risk": "none",
        "quality_flags": ["已完成异常证据核查，未发现有效异常信号"],
    }


def _blocked_gate() -> dict[str, Any]:
    return {
        "blocked": True,
        "blocked_rule_ids": ["D2-021"],
        "missing_fact_labels": ["法定代表人"],
    }


# ---------------------------------------------------------------------------
# apply_ledger_gate：只降级不升级
# ---------------------------------------------------------------------------


def test_blocked_gate_downgrades_clean_verdict_to_needs_review() -> None:
    result = _clean_verdict()

    assert apply_ledger_gate(result, _blocked_gate()) is True
    assert result["decision_state"] == "needs_review"
    assert result["review_state"] == "needs_review"
    assert result["risk_level"] is None
    assert result["overall_risk"] == "inconclusive"
    assert any("法定代表人" in flag for flag in result["quality_flags"])


def test_blocked_gate_does_not_suppress_positive_risk() -> None:
    """账本只阻断「无风险」，不得压制已经命中的真实证据。"""
    result = {
        "decision_state": "decidable",
        "review_state": "completed",
        "risk_level": "high",
        "overall_risk": "high",
        "quality_flags": [],
    }

    assert apply_ledger_gate(result, _blocked_gate()) is False
    assert result["risk_level"] == "high"
    assert result["overall_risk"] == "high"
    assert result["quality_flags"] == []


def test_unblocked_gate_keeps_clean_verdict() -> None:
    result = _clean_verdict()
    gate = {"blocked": False, "blocked_rule_ids": [], "missing_fact_labels": []}

    assert apply_ledger_gate(result, gate) is False
    assert result["overall_risk"] == "none"
    assert result["decision_state"] == "decidable"


def test_already_needs_review_is_not_touched_twice() -> None:
    """降级过的结论再次进入不得重复叠加说明。"""
    result = _clean_verdict()
    assert apply_ledger_gate(result, _blocked_gate()) is True
    flags_after_first = list(result["quality_flags"])

    assert apply_ledger_gate(result, _blocked_gate()) is False
    assert result["quality_flags"] == flags_after_first


def test_gate_flag_never_leaks_internal_rule_ids() -> None:
    """ 红线：用户可见文案禁止出现 rule_id 与机制名。"""
    result = _clean_verdict()
    gate = {
        "blocked": True,
        "blocked_rule_ids": ["D1-001", "D2-021", "D2-022"],
        "missing_fact_labels": ["法定代表人", "人员证书编号"],
    }
    apply_ledger_gate(result, gate)

    joined = " ".join(result["quality_flags"]) + str(result.get("risk_explanation", ""))
    for banned in ("D1-001", "D2-021", "D2-022", "ledger_gate", "identifier_join"):
        assert banned not in joined


def test_many_missing_labels_are_truncated_with_suffix() -> None:
    result = _clean_verdict()
    gate = {
        "blocked": True,
        "blocked_rule_ids": ["D1-001"],
        "missing_fact_labels": ["法定代表人", "统一社会信用代码", "银行账户", "人员姓名", "人员证书编号"],
    }
    apply_ledger_gate(result, gate)

    flag = next(f for f in result["quality_flags"] if "关键身份信息缺失" in f)
    assert "等" in flag
    assert "人员证书编号" not in flag


# ---------------------------------------------------------------------------
# enforce_pair_scenario_ledger：真实注册表 + 两条同形态不同实体夹具
# ---------------------------------------------------------------------------


def test_legal_rep_missing_blocks_clean_verdict(scenarios: dict[str, Any]) -> None:
    """夹具一（实体=法定代表人）：缺失导致 D2-021 不可核查，禁止落无风险。"""
    case = scenarios["legal_rep_missing"]
    result = _clean_verdict()

    enforce_pair_scenario_ledger(
        result, _doc(11, "A.pdf"), _doc(12, "B.pdf"), case["a"], case["b"]
    )

    gate = result["scenario_ledger"]["gate"]
    assert gate["blocked"] is True
    assert "D2-021" in gate["blocked_rule_ids"]
    assert "法定代表人" in gate["missing_fact_labels"]
    assert result["decision_state"] == "needs_review"
    assert result["overall_risk"] == "inconclusive"


def test_cert_missing_blocks_clean_verdict(scenarios: dict[str, Any]) -> None:
    """夹具二（实体=人员证书/身份证尾号）：同一形态换实体仍被拦住。"""
    case = scenarios["cert_missing"]
    result = _clean_verdict()

    enforce_pair_scenario_ledger(
        result, _doc(21, "C.pdf"), _doc(22, "D.pdf"), case["a"], case["b"]
    )

    gate = result["scenario_ledger"]["gate"]
    assert gate["blocked"] is True
    assert "D1-001" in gate["blocked_rule_ids"]
    assert "人员证书编号" in gate["missing_fact_labels"]
    # 本夹具企业身份字段齐备，证明闸门按缺失字段定位而非一刀切
    assert "D2-021" not in gate["blocked_rule_ids"]
    assert result["decision_state"] == "needs_review"


def test_complete_extraction_keeps_no_risk_verdict(scenarios: dict[str, Any]) -> None:
    case = scenarios["complete"]
    result = _clean_verdict()

    enforce_pair_scenario_ledger(
        result, _doc(31, "E.pdf"), _doc(32, "F.pdf"), case["a"], case["b"]
    )

    gate = result["scenario_ledger"]["gate"]
    assert gate["blocked"] is False
    assert result["decision_state"] == "decidable"
    assert result["overall_risk"] == "none"


def test_unimplemented_rules_do_not_block(scenarios: dict[str, Any]) -> None:
    """检测能力未上线的规则必须只登记、不阻断。"""
    case = scenarios["complete"]
    result = _clean_verdict()

    enforce_pair_scenario_ledger(
        result, _doc(41, "G.pdf"), _doc(42, "H.pdf"), case["a"], case["b"]
    )

    ledger = result["scenario_ledger"]
    assert ledger["counts"]["unimplemented"] > 0
    assert ledger["gate"]["blocked"] is False


def test_ledger_payload_omits_checkable_entries_but_counts_them(
    scenarios: dict[str, Any],
) -> None:
    case = scenarios["complete"]
    result = _clean_verdict()

    enforce_pair_scenario_ledger(
        result, _doc(51, "I.pdf"), _doc(52, "J.pdf"), case["a"], case["b"]
    )

    ledger = result["scenario_ledger"]
    assert ledger["counts"]["checkable"] > 0
    assert all(e["status"] != "checkable" for e in ledger["entries"])


def test_empty_extraction_blocks_instead_of_clearing(scenarios: dict[str, Any]) -> None:
    """提取整体为空时事实全缺，必须转待复核而不是判无风险。"""
    result = _clean_verdict()

    enforce_pair_scenario_ledger(
        result, _doc(61, "K.pdf"), _doc(62, "L.pdf"), None, scenarios["complete"]["b"]
    )

    assert result["scenario_ledger"]["gate"]["blocked"] is True
    assert result["overall_risk"] == "inconclusive"


def test_registry_failure_degrades_without_raising(monkeypatch: Any) -> None:
    """账本构建异常必须降级为无账本，不得阻断主对比流程。"""

    def _boom() -> None:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(
        "app.core.collusion.rules.pair_gate.load_rule_registry", _boom
    )
    result = _clean_verdict()

    enforce_pair_scenario_ledger(result, _doc(71, "M.pdf"), _doc(72, "N.pdf"), {}, {})

    assert "scenario_ledger" not in result
    assert result["overall_risk"] == "none"
