""": 评分项打被评分对象；硬指标满足即满分，主观技术方案禁止有方案即满分。

映射真实风险:
- 价格分项把评标办法复述当成已报价并给满分
- 未查到报价仍给满分
- 比较分公式在单标中编造预估分
- 技术分项「有方案」即满分
- 硬指标（满足得X/否则0）被误套技术方案压档
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.scoring

from types import SimpleNamespace

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.scoring_object_detect import (
    assessments_confirm_full_marks,
    classify_scoring_standard_kind,
    detect_quote_forms,
    has_bid_quote,
    infer_required_quote_forms,
    is_comparative_price_formula,
    is_presence_only_full_marks,
    is_price_scoring_item,
    is_technical_scoring_item,
    quote_forms_satisfy,
)
from app.core.ai.gates.review.scoring_presence_max import ScoringPresenceMaxGate
from app.core.ai.gates.review.scoring_quote_presence import ScoringQuotePresenceGate
from app.core.ai.prompts.bid_extract import BID_EXTRACT_SYSTEM
from app.core.ai.prompts.review import (
    NO_RESPONSE_PROMPT,
    REVIEW_PROMPT,
    REVIEW_SYSTEM_PROMPT,
)
from app.core.ai.reviewer_parts.scoring_rubric import scoring_supplement

_PRICE_RESTATEMENT = (
    "投标人在投标文件第2页的评标索引表中完整复述并确认接受上述价格分计算规则，"
    "具体包括报价无缺漏项、不接受选择性或附加条件报价、非招标产品不折价及修正值作评标价等核心规定，"
    "并准确明确了报价得分计算公式。"
)

_USER_PROCEDURAL_COMMENT = (
    "该要求为招标程序性规定中的评标价格分计算规则说明，属于评标办法的程序性规定，"
    "非投标文件实质性内容评审项。响应内容与招标要求完全一致，无任何实质性偏离。"
    "视为完全符合。预估得分：30分。"
)


def _price_evidence(**overrides):
    evidence = {
        "requirement_kind": "scoring_item",
        "is_key_review": True,
        "requirement_text": "投标报价价格分计算规则，满分30分，采用最低评标价法。",
        "scoring_metadata": {"volume": "price", "max_score": 30.0},
        "score_weight": 30.0,
        "category": "commercial",
        "has_response": True,
        "response_content": _PRICE_RESTATEMENT,
    }
    evidence.update(overrides)
    return evidence


def _tech_evidence(**overrides):
    evidence = {
        "requirement_kind": "scoring_item",
        "is_key_review": True,
        "requirement_text": "技术方案应有新意，体现差异性，满分20分。",
        "scoring_metadata": {"volume": "technical", "max_score": 20.0},
        "score_weight": 20.0,
        "category": "technical",
        "has_response": True,
        "response_content": "第三章 技术方案 本公司将认真组织实施。",
    }
    evidence.update(overrides)
    return evidence


def _binary_hard_indicator_evidence(**overrides):
    """图中交货期/售后类：技术卷上的二分硬指标（满足得X分，否则0）。"""
    evidence = {
        "requirement_kind": "scoring_item",
        "is_key_review": True,
        "requirement_text": (
            "交货期满足要求得2分，否则0分；"
            "质量响应承诺2小时内响应、24小时内到达得2分，否则0分。"
        ),
        "scoring_metadata": {
            "volume": "technical",
            "max_score": 4.0,
            "score_rules": [
                "交货期满足要求得2分，否则0分",
                "质量响应2小时内响应、24小时内到达得2分，否则0分",
            ],
        },
        "score_weight": 4.0,
        "category": "commercial",
        "has_response": True,
        "response_content": "承诺交货期7天；售后5分钟响应、2小时到达。",
    }
    evidence.update(overrides)
    return evidence


def _ctx(evidence, **target_fields):
    fields = {
        "compliance_level": "fully",
        "is_compliant": True,
        "score": 100,
        "estimated_score": 30.0,
        "comment": _USER_PROCEDURAL_COMMENT,
    }
    fields.update(target_fields)
    return GateContext(
        flow="investigate",
        stage="post_llm",
        target=SimpleNamespace(**fields),
        evidence=evidence,
    )


_RATE_ONLY_TENDER = (
    "最高限价为工程量清单综合单价，投标人在此基础上以总体下浮X%为报价。"
    "评标价格分=（基准价÷有效投标报价）×53分"
)
_RATE_RESPONSE = "结合第9页开标一览表显示具体下浮比例为9%。"
_AMOUNT_ONLY_TENDER = "投标总价以人民币报价，须在开标一览表填写含税总价。"
_BOTH_TENDER = "开标一览表须填写投标总价，并在最高限价基础上报出下浮率。"


class TestHasBidQuote:
    def test_rule_restatement_is_not_quote(self):
        assert has_bid_quote(_PRICE_RESTATEMENT) is False

    def test_opening_list_with_amount_is_quote(self):
        text = "开标一览表：投标总价人民币1258000.00元（含税）。"
        assert has_bid_quote(text) is True

    def test_score_formula_numbers_are_not_quote(self):
        text = "价格部分得分=100-100×n×1，满分30分，每偏离1%扣0.2分。"
        assert has_bid_quote(text) is False

    def test_empty_is_not_quote(self):
        assert has_bid_quote("") is False
        assert has_bid_quote(None) is False

    def test_discount_rate_is_quote(self):
        assert has_bid_quote(_RATE_RESPONSE) is True
        assert has_bid_quote("开标一览表下浮9%") is True

    def test_placeholder_x_percent_is_not_quote(self):
        assert has_bid_quote("明确承诺总体下浮X%（X>=0）") is False

    def test_rate_matches_rate_only_tender(self):
        assert has_bid_quote(_RATE_RESPONSE, _RATE_ONLY_TENDER) is True

    def test_rate_does_not_satisfy_amount_only_tender(self):
        assert has_bid_quote(_RATE_RESPONSE, _AMOUNT_ONLY_TENDER) is False


class TestQuoteFormMatching:
    def test_detects_rate_without_money(self):
        forms = detect_quote_forms(_RATE_RESPONSE)
        assert forms.has_rate is True
        assert forms.has_amount is False

    def test_detects_amount_and_rate_together(self):
        text = "开标一览表投标总价人民币800000元，下浮率9%。"
        forms = detect_quote_forms(text)
        assert forms.has_amount is True
        assert forms.has_rate is True

    def test_infer_rate_only_from_discount_tender(self):
        assert infer_required_quote_forms(_RATE_ONLY_TENDER) == "rate"

    def test_infer_amount_only_from_total_price_tender(self):
        assert infer_required_quote_forms(_AMOUNT_ONLY_TENDER) == "amount"

    def test_infer_both_when_tender_asks_total_and_discount(self):
        assert infer_required_quote_forms(_BOTH_TENDER) == "both"

    def test_infer_either_when_tender_only_says_bid_price(self):
        assert infer_required_quote_forms("投标报价价格分计算规则，满分30分。") == "either"

    def test_both_required_one_side_is_not_missing(self):
        provided = detect_quote_forms(_RATE_RESPONSE)
        assert quote_forms_satisfy("both", provided) is True

    def test_formula_percent_is_not_a_rate_form(self):
        forms = detect_quote_forms("价格部分得分=100-100×n×1，每偏离1%扣0.2分。")
        assert forms.has_rate is False
        assert forms.has_amount is False


class TestComparativeFormula:
    def test_lowest_bid_method_is_comparative(self):
        assert is_comparative_price_formula("采用最低评标价法，价格分30分") is True

    def test_benchmark_price_is_comparative(self):
        assert is_comparative_price_formula("评标基准价为所有有效报价的算术平均") is True

    def test_ceiling_only_is_not_comparative(self):
        text = "投标报价不得超过最高限价，超过最高限价不得分。"
        assert is_comparative_price_formula(text) is False

    def test_bare_benchmark_price_is_comparative(self):
        assert is_comparative_price_formula(_RATE_ONLY_TENDER) is True


class TestVolumeRouting:
    def test_price_volume_is_price_item(self):
        assert is_price_scoring_item(_price_evidence()) is True
        assert is_technical_scoring_item(_price_evidence()) is False

    def test_technical_volume_is_technical_item(self):
        assert is_technical_scoring_item(_tech_evidence()) is True
        assert is_price_scoring_item(_tech_evidence()) is False

    def test_normal_requirement_is_neither(self):
        ev = {"requirement_kind": "normal", "is_key_review": False, "requirement_text": "装订"}
        assert is_price_scoring_item(ev) is False
        assert is_technical_scoring_item(ev) is False


class TestScoringStandardKind:
    def test_delivery_otherwise_zero_is_binary(self):
        assert classify_scoring_standard_kind(_binary_hard_indicator_evidence()) == "binary"

    def test_novelty_language_is_graded(self):
        assert classify_scoring_standard_kind(_tech_evidence()) == "graded"

    def test_mixed_binary_and_graded_is_graded(self):
        ev = _tech_evidence(
            requirement_text="满足交货得2分否则0；技术方案有新意酌情0-18分。",
            scoring_metadata={
                "volume": "technical",
                "max_score": 20.0,
                "score_rules": [
                    "满足交货得2分，否则0分",
                    "技术方案有新意酌情0-18分",
                ],
            },
        )
        assert classify_scoring_standard_kind(ev) == "graded"

    def test_empty_is_unknown(self):
        assert classify_scoring_standard_kind({}) == "unknown"

    def test_all_met_points_confirm_full_marks(self):
        items = [
            {"status": "met", "points": 2.0},
            {"status": "met", "points": 2.0, "rule_excerpt": "盖章", "reason": ""},
        ]
        assert assessments_confirm_full_marks(items, 4.0) is True

    def test_partial_assessment_does_not_confirm_full_marks(self):
        items = [
            {"status": "met", "points": 2.0},
            {"status": "partial", "points": 2.0},
        ]
        assert assessments_confirm_full_marks(items, 4.0) is False


class TestPresenceOnlyFullMarks:
    def test_user_procedural_full_marks_is_presence_only(self):
        assert is_presence_only_full_marks(
            _USER_PROCEDURAL_COMMENT, estimated_score=30.0, max_score=30.0,
        ) is True

    def test_quality_analysis_is_not_presence_only(self):
        comment = (
            "方案针对招标差异化要求给出三项具体措施：模块化部署、国产化替代路径、"
            "与既有系统的接口差异说明，体现新意。预估得分：16分。"
        )
        assert is_presence_only_full_marks(
            comment, estimated_score=16.0, max_score=20.0,
        ) is False

    def test_below_max_is_not_presence_only(self):
        assert is_presence_only_full_marks(
            "已提供技术方案。预估得分：12分。",
            estimated_score=12.0,
            max_score=20.0,
        ) is False


class TestQuotePresenceGate:
    gate = ScoringQuotePresenceGate()

    def test_restatement_forces_zero(self):
        ctx = _ctx(_price_evidence())
        assert self.gate.applies(ctx) is True
        v = self.gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        patch = v.suggested_patch or {}
        assert patch["estimated_score"] == 0
        assert patch["compliance_level"] == "not"
        assert patch["is_compliant"] is False
        assert "未查询到报价" in patch["comment"]

    def test_missing_response_forces_zero(self):
        ctx = _ctx(_price_evidence(has_response=False, response_content=""))
        v = self.gate.evaluate(ctx)
        assert (v.suggested_patch or {})["estimated_score"] == 0
        assert "未查询到报价" in (v.suggested_patch or {})["comment"]

    def test_real_quote_comparative_clears_estimate(self):
        ctx = _ctx(
            _price_evidence(
                response_content="开标一览表投标总价人民币1,258,000.00元。",
            ),
            estimated_score=30.0,
            comment="已报价。预估得分：30分。",
        )
        v = self.gate.evaluate(ctx)
        patch = v.suggested_patch or {}
        assert patch.get("estimated_score") is None
        assert "比较分待评标" in patch["comment"]
        assert "预估得分：30分" not in patch["comment"]

    def test_real_quote_ceiling_only_does_not_force_zero(self):
        ctx = _ctx(
            _price_evidence(
                requirement_text="投标报价不得超过最高限价，超过不得分。满分10分。",
                scoring_metadata={"volume": "price", "max_score": 10.0},
                score_weight=10.0,
                response_content="投标总价人民币800000元，未超过最高限价。",
            ),
            estimated_score=8.0,
            comment="报价未超限价。预估得分：8分。",
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_not_applicable_for_technical(self):
        ctx = _ctx(_tech_evidence(), estimated_score=20.0, comment="有方案")
        assert self.gate.applies(ctx) is False

    def test_discount_rate_does_not_force_zero(self):
        """: 招标只要下浮率时，开标一览表 9% 不得整段改判未报价/0分。"""
        llm_comment = (
            "第9页开标一览表列明下浮比例9%，已按招标要求以总体下浮报价。"
            "预估得分：40分。"
        )
        ctx = _ctx(
            _price_evidence(
                requirement_text=_RATE_ONLY_TENDER,
                scoring_metadata={"volume": "price", "max_score": 53.0},
                score_weight=53.0,
                response_content=_RATE_RESPONSE,
            ),
            estimated_score=40.0,
            score=80,
            compliance_level="fully",
            is_compliant=True,
            comment=llm_comment,
        )
        v = self.gate.evaluate(ctx)
        patch = v.suggested_patch or {}
        assert patch.get("estimated_score") is None
        assert "未查询到报价" not in (patch.get("comment") or llm_comment)
        assert "比较分待评标" in patch["comment"]
        assert "不代表实际评标得分" in patch["comment"]
        assert "下浮比例9%" in patch["comment"]
        assert "compliance_level" not in patch
        assert "is_compliant" not in patch

    def test_both_required_rate_only_does_not_force_zero(self):
        ctx = _ctx(
            _price_evidence(
                requirement_text=_BOTH_TENDER,
                scoring_metadata={"volume": "price", "max_score": 10.0},
                score_weight=10.0,
                response_content=_RATE_RESPONSE,
            ),
            estimated_score=8.0,
            comment="已报下浮9%。预估得分：8分。",
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_amount_required_rate_only_still_forces_zero(self):
        ctx = _ctx(
            _price_evidence(
                requirement_text=_AMOUNT_ONLY_TENDER,
                scoring_metadata={"volume": "price", "max_score": 10.0},
                score_weight=10.0,
                response_content=_RATE_RESPONSE,
            ),
            estimated_score=8.0,
            comment="仅见下浮9%。预估得分：8分。",
        )
        v = self.gate.evaluate(ctx)
        patch = v.suggested_patch or {}
        assert v.verdict == "enforce_patch"
        assert patch["estimated_score"] == 0
        assert patch["compliance_level"] == "not"
        assert "未查询到报价" in patch["comment"]


class TestPresenceMaxGate:
    gate = ScoringPresenceMaxGate()

    def test_presence_only_caps_full_marks(self):
        ctx = _ctx(
            _tech_evidence(),
            estimated_score=20.0,
            comment="投标文件已提供技术方案，视为完全符合。预估得分：20分。",
        )
        assert self.gate.applies(ctx) is True
        v = self.gate.evaluate(ctx)
        patch = v.suggested_patch or {}
        assert patch["estimated_score"] == 12.0
        assert patch["compliance_level"] == "partially"
        assert "有技术方案不等于满分" in patch["comment"]
        assert "预估得分：20分" not in patch["comment"]

    def test_quality_backed_full_marks_pass(self):
        ctx = _ctx(
            _tech_evidence(),
            estimated_score=20.0,
            comment=(
                "方案针对差异性给出国产化替代与接口适配两项具体措施，"
                "并说明创新点。预估得分：20分。"
            ),
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"

    def test_not_applicable_for_price(self):
        ctx = _ctx(_price_evidence())
        assert self.gate.applies(ctx) is False

    def test_binary_hard_indicator_full_marks_not_capped(self):
        """: 技术卷上的「满足得X/否则0」不得按技术方案压到 60%。"""
        ctx = _ctx(
            _binary_hard_indicator_evidence(),
            estimated_score=4.0,
            comment=(
                "投标人承诺交货期7天，满足交货要求应得2分；"
                "售后5分钟响应、2小时到达，优于招标要求应得2分。"
                "预估得分：4分。"
            ),
        )
        assert self.gate.applies(ctx) is True
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"
        assert v.suggested_patch is None

    def test_all_met_assessments_skip_presence_cap(self):
        """逐档已全部达标且分值合计满分时，不得再按存在性压档。"""
        ctx = _ctx(
            _tech_evidence(),
            estimated_score=20.0,
            comment="已提供技术方案。预估得分：20分。",
            rule_assessments=[
                {
                    "rule_excerpt": "新意",
                    "status": "met",
                    "points": 10.0,
                    "reason": "有具体创新点",
                    "evidence_pages": [3],
                },
                {
                    "rule_excerpt": "差异性",
                    "status": "met",
                    "points": 10.0,
                    "reason": "有接口差异说明",
                    "evidence_pages": [4],
                },
            ],
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "pass"
        assert v.suggested_patch is None

    def test_mixed_graded_and_binary_still_caps_presence_only(self):
        """混有新意/酌情等分档语言时，仍按 压存在性满分。"""
        ctx = _ctx(
            _tech_evidence(
                requirement_text="满足交货得2分否则0；技术方案有新意酌情0-18分。",
                scoring_metadata={
                    "volume": "technical",
                    "max_score": 20.0,
                    "score_rules": [
                        "满足交货得2分，否则0分",
                        "技术方案有新意酌情0-18分",
                    ],
                },
            ),
            estimated_score=20.0,
            comment="已提供技术方案。预估得分：20分。",
        )
        v = self.gate.evaluate(ctx)
        assert v.verdict == "enforce_patch"
        assert (v.suggested_patch or {})["estimated_score"] == 12.0


class TestPostLlmEvidenceWiring:
    def test_base_evidence_includes_scoring_fields(self):
        from app.core.ai.reviewer_parts.result_builder import _base_review_gate_evidence

        pair = SimpleNamespace(
            is_mandatory=False,
            has_response=True,
            category="commercial",
            response=SimpleNamespace(content="开标一览表投标总价人民币1元"),
            requirement=SimpleNamespace(
                requirement_kind="scoring_item",
                is_key_review=1,
                description="价格分计算规则",
                scoring_metadata={"volume": "price", "max_score": 30},
                score_weight=30,
            ),
        )
        ev = _base_review_gate_evidence(pair)
        assert ev["requirement_kind"] == "scoring_item"
        assert ev["scoring_metadata"]["volume"] == "price"
        assert "投标总价" in ev["response_content"]

    def test_investigate_pipeline_zeros_price_restatement(self):
        from app.core.ai.gates import build_pipeline_for

        target = SimpleNamespace(
            compliance_level="fully",
            is_compliant=True,
            score=100,
            estimated_score=30.0,
            comment=_USER_PROCEDURAL_COMMENT,
            relevance_score=90,
        )
        ctx = GateContext(
            flow="investigate",
            stage="post_llm",
            target=target,
            evidence=_price_evidence(),
        )
        build_pipeline_for("investigate").run(ctx, apply_patch_to=target)
        assert target.estimated_score == 0
        assert target.compliance_level == "not"
        assert "未查询到报价" in target.comment

    def test_investigate_pipeline_keeps_discount_rate_quote(self):
        from app.core.ai.gates import build_pipeline_for

        target = SimpleNamespace(
            compliance_level="fully",
            is_compliant=True,
            score=80,
            estimated_score=40.0,
            comment="第9页开标一览表列明下浮比例9%。预估得分：40分。",
            relevance_score=90,
        )
        ctx = GateContext(
            flow="investigate",
            stage="post_llm",
            target=target,
            evidence=_price_evidence(
                requirement_text=_RATE_ONLY_TENDER,
                scoring_metadata={"volume": "price", "max_score": 53.0},
                score_weight=53.0,
                response_content=_RATE_RESPONSE,
            ),
        )
        build_pipeline_for("investigate").run(ctx, apply_patch_to=target)
        assert target.estimated_score is None
        assert target.compliance_level == "fully"
        assert target.is_compliant is True
        assert "未查询到报价" not in target.comment
        assert "比较分待评标" in target.comment
    def test_review_prompts_forbid_scoring_item_procedural_full_marks(self):
        blob = REVIEW_SYSTEM_PROMPT + REVIEW_PROMPT + NO_RESPONSE_PROMPT
        assert "评分项不得按程序性规定直接给满分" in blob
        assert "复述评标办法不等于报价" in blob
        assert "有技术方案不等于满分" in blob
        assert "硬指标" in blob
        assert "满足即满分" in blob

    def test_bid_extract_rejects_rule_restatement_as_quote(self):
        assert "复述评标办法不等于报价" in BID_EXTRACT_SYSTEM
        assert "下浮" in BID_EXTRACT_SYSTEM

    def test_price_supplement_requires_quote_object(self):
        pair = SimpleNamespace(
            requirement=SimpleNamespace(
                requirement_kind="scoring_item",
                is_key_review=1,
                score_weight=30,
                description="价格分计算规则",
                scoring_metadata={"volume": "price", "max_score": 30},
            )
        )
        text = scoring_supplement(pair)
        assert "未查询到报价" in text
        assert "比较分待评标" in text
        assert "下浮" in text

    def test_technical_supplement_forbids_presence_max(self):
        pair = SimpleNamespace(
            requirement=SimpleNamespace(
                requirement_kind="scoring_item",
                is_key_review=1,
                score_weight=20,
                description="技术方案应有新意",
                scoring_metadata={"volume": "technical", "max_score": 20},
            )
        )
        text = scoring_supplement(pair)
        assert "有技术方案不等于满分" in text

    def test_binary_hard_indicator_supplement_requires_full_marks(self):
        pair = SimpleNamespace(
            requirement=SimpleNamespace(
                requirement_kind="scoring_item",
                is_key_review=1,
                score_weight=4,
                description="交货期满足要求得2分，否则0分",
                scoring_metadata={
                    "volume": "technical",
                    "max_score": 4,
                    "score_rules": [
                        "交货期满足要求得2分，否则0分",
                        "质量响应2小时内响应得2分，否则0分",
                    ],
                },
            )
        )
        text = scoring_supplement(pair)
        assert "有技术方案不等于满分" not in text
        assert "硬指标" in text
        assert "满足即满分" in text
