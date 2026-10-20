"""审查结论字段一致性 防御测试

锁定确证缺陷: ExtractedReviewResult 的 is_compliant 与 compliance_level 缺跨字段
约束, LLM 幻觉可落库自相矛盾的审查结论(高业务风险输出)。model_validator 以语义
更细的 compliance_level 为权威纠正二元 is_compliant, partially 灰区保留 LLM 值。

纯 Pydantic 单元测试, 不依赖数据库。
"""

import pytest

from app.schemas.review import ExtractedReviewResult


def _make(**overrides) -> ExtractedReviewResult:
    base = dict(
        requirement_id=1,
        category="technical",
        requirement_summary="资质要求摘要",
        response_summary="投标响应摘要",
        is_compliant=True,
        compliance_level="fully",
        score=80,
        comment="十个字符以上的详细审查意见内容",
        risk_level="none",
    )
    base.update(overrides)
    return ExtractedReviewResult(**base)


def test_fully_forces_compliant_true():
    """compliance_level=fully 但 LLM 给 is_compliant=False -> 纠正为 True。"""
    assert _make(compliance_level="fully", is_compliant=False).is_compliant is True


@pytest.mark.parametrize("level", ["not", "missing"])
def test_not_or_missing_forces_compliant_false(level):
    """compliance_level=not/missing 但 LLM 给 is_compliant=True -> 纠正为 False。"""
    assert _make(compliance_level=level, is_compliant=True, risk_level="high").is_compliant is False


def test_partially_preserves_llm_value():
    """partially 为灰区, 两种 is_compliant 取值均保留, 不强制。"""
    assert _make(compliance_level="partially", is_compliant=True).is_compliant is True
    assert _make(compliance_level="partially", is_compliant=False).is_compliant is False


def test_consistent_input_unchanged():
    """正常一致输入不被改动(回归保护)。"""
    assert _make(compliance_level="fully", is_compliant=True).is_compliant is True
    assert _make(compliance_level="not", is_compliant=False).is_compliant is False
    assert _make(compliance_level="missing", is_compliant=False, score=0).is_compliant is False
