"""审查输出契约瘦身防御测试

验证 ReviewJudgementPayload 最小输出契约的正确性，
以及 assemble_review_result 装配行为的正常/边界/异常场景。

不引入数据库：使用 SimpleNamespace 构造轻量 MatchedPair stub，
只包含 assemble_review_result 实际访问的属性。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from app.schemas.review import ReviewJudgementPayload
from app.core.ai.validators import (
    get_review_judgement_validator,
    get_review_result_validator,
)
from app.core.ai.reviewer_parts.result_assembly import assemble_review_result


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _min_payload_json(**overrides: Any) -> str:
    """构造最小合法新契约 JSON（仅 4 个必填字段，可按需覆盖）。"""
    data: dict[str, Any] = {
        "compliance_level": "fully",
        "score": 80,
        "comment": "投标响应完全满足招标要求，资质证明材料齐全有效。",
        "risk_level": "none",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def _make_pair(
    *,
    requirement_id: int = 42,
    response_id: Optional[int] = 99,
    category: str = "technical",
    requirement_description: Optional[str] = "测试招标要求描述",
    response_content: Optional[str] = "测试投标响应内容",
) -> SimpleNamespace:
    """构造轻量 pair stub，仅含 assemble_review_result 实际访问的属性。

    传入 requirement_description=None 模拟 pair.requirement 为 None；
    传入 response_content=None 模拟 pair.response 为 None。
    """
    requirement = (
        SimpleNamespace(description=requirement_description)
        if requirement_description is not None
        else None
    )
    response = (
        SimpleNamespace(content=response_content)
        if response_content is not None
        else None
    )
    return SimpleNamespace(
        requirement_id=requirement_id,
        response_id=response_id,
        category=category,
        requirement=requirement,
        response=response,
    )


def _min_payload(**kwargs: Any) -> ReviewJudgementPayload:
    """直接构造最小合法 ReviewJudgementPayload 对象（绕过 JSON 解析）。"""
    defaults: dict[str, Any] = {
        "compliance_level": "fully",
        "score": 80,
        "comment": "投标响应完全满足招标要求，资质证明材料齐全有效。",
        "risk_level": "none",
    }
    defaults.update(kwargs)
    return ReviewJudgementPayload(**defaults)


# ===========================================================================
# A. slim 契约本身（场景 1-8）
# ===========================================================================


class TestSlimContractValidation:
    """ReviewJudgementPayload 最小输出契约校验——新旧契约对照"""

    def test_slim_contract_accepts_four_required_fields_only(self) -> None:
        """场景1：仅 4 个必填字段的 JSON 能被新契约校验通过。"""
        validator = get_review_judgement_validator()
        result = validator.validate(_min_payload_json())
        assert result.success, (
            f"新契约在仅提供 4 个必填字段时不应失败，实际错误：{result.error}"
        )
        payload = result.unwrap()
        assert payload.compliance_level == "fully", (
            "compliance_level 应为 fully"
        )
        assert payload.score == 80, "score 应为 80"

    def test_slim_contract_accepts_payload_without_server_side_fields(self) -> None:
        """场景2：完全不提供 requirement_id/bid_response_id/category/requirement_summary 时新契约通过。

 修复核心：这些服务端已知字段不再进入模型失败面，
 模型无法输出它们时不应触发校验失败和重放。
 """
        validator = get_review_judgement_validator()
        raw = json.dumps(
            {
                "compliance_level": "partially",
                "score": 60,
                "comment": "投标对技术方案描述不够详细，缺少部分关键指标说明。",
                "risk_level": "medium",
                # 显式不含 requirement_id/bid_response_id/category/requirement_summary
            },
            ensure_ascii=False,
        )
        result = validator.validate(raw)
        assert result.success, (
            "新契约在缺少 requirement_id/category 等服务端字段时不应失败，"
            f"实际错误：{result.error}"
        )

    def test_legacy_contract_rejects_payload_missing_server_fields(self) -> None:
        """场景3（对照断言）：同一个 4 字段 payload 用旧契约校验应失败。

        证明契约确实瘦身了，不是测试写松了——旧契约要求 requirement_id/
        category/requirement_summary/response_summary/is_compliant 必填。
        """
        old_validator = get_review_result_validator()
        result = old_validator.validate(_min_payload_json())
        assert not result.success, (
            "旧契约在缺少 requirement_id/category/requirement_summary/"
            "response_summary/is_compliant 时应校验失败，但实际通过了"
        )

    def test_slim_contract_ignores_extra_legacy_fields_echoed_by_model(self) -> None:
        """场景4：模型仍回显 requirement_id/category/requirement_summary/source_pages 等旧字段，
        新契约校验通过且额外字段被 Pydantic 忽略（宽进严出设计）。
        """
        validator = get_review_judgement_validator()
        raw = json.dumps(
            {
                "requirement_id": 123,
                "bid_response_id": 456,
                "category": "qualification",
                "requirement_summary": "资质要求摘要，不超过五十字",
                "response_summary": "投标响应摘要",
                "is_compliant": True,
                "source_pages": "第3页",
                "compliance_level": "fully",
                "score": 90,
                "comment": "投标方完全满足资质要求，证书齐全有效。",
                "risk_level": "none",
            },
            ensure_ascii=False,
        )
        result = validator.validate(raw)
        assert result.success, (
            f"新契约在模型回显旧字段时不应失败，实际错误：{result.error}"
        )
        payload = result.unwrap()
        # 额外字段被 Pydantic 忽略，不应出现在 payload 实例上
        assert not hasattr(payload, "requirement_id"), (
            "requirement_id 不在新契约中，不应出现在 payload 属性上"
        )
        assert not hasattr(payload, "category"), (
            "category 不在新契约中，不应出现在 payload 属性上"
        )
        assert not hasattr(payload, "source_pages"), (
            "source_pages 不在新契约中，不应出现在 payload 属性上"
        )

    def test_long_response_summary_passes_slim_but_fails_legacy(self) -> None:
        """场景5：response_summary 给 300+ 字符的长串，新契约通过，旧契约因 max_length=100 失败。

        旧契约 response_summary max_length=100 曾造成大量纯长度失败并触发 LLM 整条重放。
        """
        long_summary = "投标响应详细说明" * 40  # 320 个字符
        assert len(long_summary) >= 300, (
            f"测试前置：long_summary 须 >= 300 字符，实际 {len(long_summary)}"
        )

        # 新契约：无长度上限，应通过
        new_validator = get_review_judgement_validator()
        new_raw = json.dumps(
            {
                "compliance_level": "partially",
                "score": 70,
                "comment": "投标响应基本满足要求，但部分技术参数描述不够精确。",
                "risk_level": "low",
                "response_summary": long_summary,
            },
            ensure_ascii=False,
        )
        new_result = new_validator.validate(new_raw)
        assert new_result.success, (
            f"新契约对 {len(long_summary)} 字符的 response_summary 不应失败，"
            f"实际错误：{new_result.error}"
        )

        # 旧契约：max_length=100，应失败
        old_validator = get_review_result_validator()
        old_raw = json.dumps(
            {
                "requirement_id": 1,
                "bid_response_id": 2,
                "category": "technical",
                "requirement_summary": "技术方案要求摘要",
                "response_summary": long_summary,  # 超出旧契约 max_length=100
                "is_compliant": True,
                "compliance_level": "partially",
                "score": 70,
                "comment": "投标响应基本满足要求，但部分技术参数描述不够精确。",
                "risk_level": "low",
            },
            ensure_ascii=False,
        )
        old_result = old_validator.validate(old_raw)
        assert not old_result.success, (
            "旧契约对超过 100 字符的 response_summary 应校验失败，但实际通过了"
        )

    def test_short_comment_below_min_length_fails_validation(self) -> None:
        """场景6：comment 只有 3 个汉字（不足 10 字符）时校验失败，质量底线必须保留。"""
        validator = get_review_judgement_validator()
        result = validator.validate(_min_payload_json(comment="太短了"))
        assert not result.success, (
            "comment 字符数不足 min_length=10 时新契约应校验失败，但实际通过了"
        )

    def test_score_above_upper_bound_fails_validation(self) -> None:
        """场景7a：score=101 超出 le=100 上界时校验失败。"""
        validator = get_review_judgement_validator()
        result = validator.validate(_min_payload_json(score=101))
        assert not result.success, (
            "score=101 超出 le=100 约束，新契约应校验失败，但实际通过了"
        )

    def test_score_below_lower_bound_fails_validation(self) -> None:
        """场景7b：score=-1 低于 ge=0 下界时校验失败。"""
        validator = get_review_judgement_validator()
        result = validator.validate(_min_payload_json(score=-1))
        assert not result.success, (
            "score=-1 低于 ge=0 约束，新契约应校验失败，但实际通过了"
        )

    def test_invalid_compliance_level_enum_fails_validation(self) -> None:
        """场景8：compliance_level 为非法枚举值时校验失败。"""
        validator = get_review_judgement_validator()
        result = validator.validate(_min_payload_json(compliance_level="unknown_value"))
        assert not result.success, (
            "非法 compliance_level 值应校验失败，但实际通过了"
        )


# ===========================================================================
# B. 装配行为（场景 9-16）
# ===========================================================================


class TestAssembleReviewResult:
    """assemble_review_result 装配行为——权威字段与截断规则验证"""

    def test_assemble_takes_requirement_id_from_pair_not_payload(self) -> None:
        """场景9：装配结果的 requirement_id 必须取自 pair，绝不采信模型回显。"""
        pair = _make_pair(requirement_id=777)
        payload = _min_payload(comment="完全符合招标要求，资质文件齐全。")
        result = assemble_review_result(payload, pair)
        assert result.requirement_id == 777, (
            f"requirement_id 应来自 pair(777)，实际得到 {result.requirement_id}"
        )

    def test_requirement_summary_truncated_to_50_chars(self) -> None:
        """场景10：pair.requirement.description 超 50 字时，结果截断为 description[:50]。"""
        long_desc = (
            "这是一段超过五十个字符的招标要求详细描述，包含了许多"
            "技术规格和质量标准要求，投标方必须逐一满足所有条款内容。"
        )
        assert len(long_desc) > 50, (
            f"测试前置：long_desc 须 > 50 字符，实际 {len(long_desc)}"
        )
        pair = _make_pair(requirement_description=long_desc)
        payload = _min_payload(comment="投标响应基本满足要求，详情见审查意见说明。")
        result = assemble_review_result(payload, pair)

        assert len(result.requirement_summary) == 50, (
            f"超 50 字的 description 应截断为 50 字，实际长度 {len(result.requirement_summary)}"
        )
        assert result.requirement_summary == long_desc[:50], (
            f"截断结果应为 description 前 50 字，"
            f"期望：{long_desc[:50]!r}，实际：{result.requirement_summary!r}"
        )

    def test_response_summary_truncated_to_100_chars(self) -> None:
        """场景11：payload.response_summary 超 100 字时，装配结果截断为前 100 字。"""
        long_response = "投标方对技术方案的详细说明内容如下，包含多项技术指标。" * 10
        assert len(long_response) > 100, (
            f"测试前置：long_response 须 > 100 字符，实际 {len(long_response)}"
        )
        pair = _make_pair()
        payload = _min_payload(
            comment="投标响应基本满足要求，但部分参数有待进一步确认。",
            response_summary=long_response,
        )
        result = assemble_review_result(payload, pair)

        assert len(result.response_summary) == 100, (
            f"超 100 字的 response_summary 应截断为 100 字，实际 {len(result.response_summary)}"
        )
        assert result.response_summary == long_response[:100], (
            "截断结果应为 response_summary 前 100 字"
        )

    def test_response_summary_falls_back_to_pair_response_content(self) -> None:
        """场景12：payload 未提供 response_summary 时，从 pair.response.content 回填。"""
        content = "这是来自投标文件的响应原文内容，篇幅较短。"
        pair = _make_pair(response_content=content)
        payload = _min_payload(
            comment="投标响应基本满足招标要求，已核实相关资质证明。",
            response_summary=None,
        )
        result = assemble_review_result(payload, pair)
        assert result.response_summary == content, (
            f"payload 无 response_summary 时应回填 pair.response.content，"
            f"期望：{content!r}，实际：{result.response_summary!r}"
        )

    def test_response_summary_uses_placeholder_when_both_sides_empty(self) -> None:
        """场景13：payload 与 pair.response 都无内容时，response_summary 为固定占位串。"""
        pair = _make_pair(response_content=None)  # pair.response = None
        payload = _min_payload(
            compliance_level="missing",
            score=0,
            comment="投标文件对此条款未作任何响应，属于严重缺项。",
            risk_level="high",
            response_summary=None,
        )
        result = assemble_review_result(payload, pair)
        assert result.response_summary == "（模型未提供响应摘要）", (
            f"两侧都无内容时应落到占位串，实际：{result.response_summary!r}"
        )

    def test_assemble_with_null_requirement_yields_empty_summary(self) -> None:
        """场景14：pair.requirement 为 None 时装配不抛任何异常，requirement_summary 为空串。"""
        pair = _make_pair(requirement_description=None)  # pair.requirement = None
        payload = _min_payload(
            compliance_level="missing",
            score=0,
            comment="投标文件对此要求无任何响应，属严重缺失项目。",
            risk_level="high",
        )
        # 不应抛出任何异常
        result = assemble_review_result(payload, pair)
        assert result.requirement_summary == "", (
            f"pair.requirement 为 None 时 requirement_summary 应为空串，"
            f"实际：{result.requirement_summary!r}"
        )

    def test_derive_is_compliant_fully_yields_true(self) -> None:
        """场景15a：compliance_level=fully 时，is_compliant 最终值为 True。

        _derive_is_compliant 返回 True，ExtractedReviewResult 的 model_validator 同样强制 True。
        """
        pair = _make_pair()
        payload = _min_payload(
            compliance_level="fully",
            score=95,
            comment="投标方完全满足所有技术要求，资质证明材料齐全有效。",
            risk_level="none",
        )
        result = assemble_review_result(payload, pair)
        assert result.is_compliant is True, (
            f"fully 应推导为 is_compliant=True，实际 {result.is_compliant}"
        )

    def test_derive_is_compliant_not_yields_false(self) -> None:
        """场景15b：compliance_level=not 时，is_compliant 最终值为 False。

        _derive_is_compliant 返回 False，model_validator 同样强制 False。
        """
        pair = _make_pair()
        payload = _min_payload(
            compliance_level="not",
            score=20,
            comment="投标方未满足核心技术指标要求，存在重大缺陷无法通过。",
            risk_level="high",
        )
        result = assemble_review_result(payload, pair)
        assert result.is_compliant is False, (
            f"not 应推导为 is_compliant=False，实际 {result.is_compliant}"
        )

    def test_derive_is_compliant_missing_yields_false(self) -> None:
        """场景15c：compliance_level=missing 时，is_compliant 最终值为 False。

        _derive_is_compliant 返回 False，model_validator 同样强制 False。
        """
        pair = _make_pair()
        payload = _min_payload(
            compliance_level="missing",
            score=0,
            comment="投标文件对此项要求完全没有响应，属于缺项严重违规。",
            risk_level="high",
        )
        result = assemble_review_result(payload, pair)
        assert result.is_compliant is False, (
            f"missing 应推导为 is_compliant=False，实际 {result.is_compliant}"
        )

    def test_derive_is_compliant_partially_with_model_true_yields_true(self) -> None:
        """场景15d：compliance_level=partially 且模型给 is_compliant=True 时，最终值为 True。

        partially 属灰区，沿用模型给定值；model_validator 对 partially 不强制。
        """
        pair = _make_pair()
        payload = _min_payload(
            compliance_level="partially",
            score=65,
            comment="投标方部分响应了技术要求，核心指标基本达标但细节不足。",
            risk_level="medium",
            is_compliant=True,
        )
        result = assemble_review_result(payload, pair)
        assert result.is_compliant is True, (
            f"partially+模型True 应保留 is_compliant=True，实际 {result.is_compliant}"
        )

    def test_derive_is_compliant_partially_with_model_none_yields_false(self) -> None:
        """场景15e：compliance_level=partially 且模型未提供 is_compliant 时，最终值为 False。

        partially 无模型值时保守判为不符合（bool(None) 为 False）。
        """
        pair = _make_pair()
        payload = _min_payload(
            compliance_level="partially",
            score=55,
            comment="投标方对部分条款有响应，但关键技术参数描述缺失明显。",
            risk_level="medium",
            is_compliant=None,
        )
        result = assemble_review_result(payload, pair)
        assert result.is_compliant is False, (
            f"partially+模型None 应保守推导为 is_compliant=False，实际 {result.is_compliant}"
        )

    def test_optional_fields_passed_through_when_provided(self) -> None:
        """场景16a：estimated_score/rule_assessments/relevance_score/relevance_note 给值时，
        装配结果与 payload 一致。
        """
        from app.schemas.review import RuleAssessmentItem

        pair = _make_pair()
        rule_items = [
            RuleAssessmentItem(
                rule_excerpt="投标方须提供三年内类似业绩证明",
                status="met",
                points=10.0,
                reason="已提供 5 份合同复印件，金额达标",
                evidence_pages=[5, 6],
            )
        ]
        payload = _min_payload(
            comment="投标响应整体完整，规则评估详情见附件列表。",
            estimated_score=8.5,
            rule_assessments=rule_items,
            relevance_score=85,
            relevance_note="响应内容与招标要求领域高度相关",
        )
        result = assemble_review_result(payload, pair)

        assert result.estimated_score == 8.5, (
            f"estimated_score 应透传为 8.5，实际 {result.estimated_score}"
        )
        assert result.relevance_score == 85, (
            f"relevance_score 应透传为 85，实际 {result.relevance_score}"
        )
        assert result.relevance_note == "响应内容与招标要求领域高度相关", (
            f"relevance_note 应透传，实际 {result.relevance_note!r}"
        )
        assert result.rule_assessments is not None, (
            "rule_assessments 给值时不应为 None"
        )
        assert len(result.rule_assessments) == 1, (
            f"rule_assessments 应含 1 条，实际 {len(result.rule_assessments)}"
        )

    def test_optional_fields_are_none_when_not_provided(self) -> None:
        """场景16b：estimated_score/rule_assessments/relevance_score/relevance_note 不给时，
        装配结果对应字段均为 None。
        """
        pair = _make_pair()
        # _min_payload 默认不设可选字段，均为 None
        payload = _min_payload(comment="投标文件对此项要求基本满足，无特殊备注说明。")
        result = assemble_review_result(payload, pair)

        assert result.estimated_score is None, (
            f"未提供 estimated_score 时应为 None，实际 {result.estimated_score}"
        )
        assert result.rule_assessments is None, (
            f"未提供 rule_assessments 时应为 None，实际 {result.rule_assessments}"
        )
        assert result.relevance_score is None, (
            f"未提供 relevance_score 时应为 None，实际 {result.relevance_score}"
        )
        assert result.relevance_note is None, (
            f"未提供 relevance_note 时应为 None，实际 {result.relevance_note}"
        )
