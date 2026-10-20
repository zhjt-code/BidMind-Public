"""Contract tests for ReviewOrchestrator._compute_algorithm_evidence().

这组测试锁定 reviewer 向下游 Gate 注入的证据类型契约, 回归修复
"'str' object has no attribute 'confidence'": 历史实现把
compare_amount() 的字符串返回值塞进 amount_verdict, 下游
derive_risk_band() 访问 .confidence 直接崩掉整条审查链路。

断言三件事:
1. amount_verdict 必须是 AmountVerdict | None, 绝不为 str
2. date_verdict / relevance_result 保持既有对象类型契约
3. 新增 amount_relation 字段保留比较关系供审计
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.ai.reviewer import ReviewOrchestrator
from app.core.extractor.tools.amount_parser import AmountVerdict
from app.core.extractor.tools.date_range_checker import DateRangeVerdict
from app.core.matcher.relevance_scorer import RelevanceScore


def _fake_pair(req_desc: str, resp_content: str, category: str = "COMM"):
    """构造最小可用的 MatchedPair 替身, 只暴露被测函数读取的属性."""
    requirement = SimpleNamespace(description=req_desc)
    response = SimpleNamespace(content=resp_content)
    return SimpleNamespace(
        requirement=requirement,
        response=response,
        category=category,
    )


class TestAmountVerdictContract:
    """回归测试: amount_verdict 类型必须与 derive_risk_band() 契约一致."""

    def test_amount_verdict_is_amountverdict_when_requirement_has_amount(self):
        """招标要求含金额时, amount_verdict 必须是 AmountVerdict 对象."""
        pair = _fake_pair(
            req_desc="项目预算 500 万元, 投标报价不得高于此金额",
            resp_content="我方报价 480 万元",
        )
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)

        assert "amount_verdict" in evidence
        amount = evidence["amount_verdict"]
        assert amount is None or isinstance(amount, AmountVerdict), (
            f"amount_verdict 必须是 AmountVerdict | None, 实际为 {type(amount).__name__}"
        )
        assert not isinstance(amount, str), (
            "amount_verdict 绝不能是字符串, 否则下游 .confidence 访问会崩"
        )
        assert isinstance(amount, AmountVerdict)
        assert amount.canonical_value is not None
        assert hasattr(amount, "confidence")

    def test_amount_verdict_is_none_when_requirement_has_no_amount(self):
        """招标要求不含金额时, amount_verdict 为 None, 视为维度不适用."""
        pair = _fake_pair(
            req_desc="投标人须具有 ISO9001 质量管理体系认证",
            resp_content="我方已取得 ISO9001 认证",
        )
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)
        assert evidence["amount_verdict"] is None

    def test_amount_verdict_consumable_by_derive_risk_band(self):
        """reviewer 产出的 evidence 直接喂给 derive_risk_band 不抛异常."""
        from app.core.ai.risk_band import derive_risk_band

        pair = _fake_pair(
            req_desc="合同金额 1000 万元",
            resp_content="报价 950 万元",
        )
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)
        rb = derive_risk_band(
            is_mandatory=True,
            date_verdict=evidence.get("date_verdict"),
            amount_verdict=evidence.get("amount_verdict"),
            relevance=evidence.get("relevance_result"),
        )
        assert rb.risk_band in ("high", "medium", "low", "none")


class TestAmountRelationAuditField:
    """新增 amount_relation 字段必须独立保留 compare_amount() 的比较结果."""

    def test_amount_relation_present_when_requirement_has_amount(self):
        pair = _fake_pair(
            req_desc="项目预算 500 万元",
            resp_content="报价 400 万元",
        )
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)
        assert "amount_relation" in evidence
        assert evidence["amount_relation"] in (
            "meets", "below", "above", "unparseable", None,
        )

    def test_amount_relation_below(self):
        pair = _fake_pair(
            req_desc="预算上限 500 万元",
            resp_content="报价 400 万元",
        )
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)
        assert evidence["amount_relation"] == "below"

    def test_amount_relation_none_when_no_amount(self):
        pair = _fake_pair(
            req_desc="投标文件须加盖公章",
            resp_content="已盖章",
        )
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)
        assert evidence["amount_relation"] is None


class TestOtherEvidenceTypesUnchanged:
    """同批排查: date_verdict / relevance_result 类型契约不能回归."""

    def test_date_verdict_type_is_daterangeverdict_or_none(self):
        pair = _fake_pair(
            req_desc="近三年完成的类似项目",
            resp_content="合同签订日期 2024 年 3 月",
        )
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)
        dv = evidence.get("date_verdict")
        assert dv is None or isinstance(dv, DateRangeVerdict), (
            f"date_verdict 契约破坏: {type(dv).__name__}"
        )

    def test_relevance_result_type_is_relevancescore_or_none(self):
        pair = _fake_pair(
            req_desc="具有类似项目业绩",
            resp_content="我方承担过多个同类项目",
            category="PERF",
        )
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)
        rel = evidence.get("relevance_result")
        assert rel is None or isinstance(rel, RelevanceScore), (
            f"relevance_result 契约破坏: {type(rel).__name__}"
        )


class TestEmptyInputs:
    """边界: 空响应 / 空要求也不能抛异常."""

    def test_empty_response(self):
        pair = _fake_pair(req_desc="预算 500 万元", resp_content="")
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)
        amount = evidence["amount_verdict"]
        assert amount is None or isinstance(amount, AmountVerdict)

    def test_empty_requirement(self):
        pair = _fake_pair(req_desc="", resp_content="报价 400 万元")
        evidence = ReviewOrchestrator._compute_algorithm_evidence(pair)
        assert evidence["amount_verdict"] is None
