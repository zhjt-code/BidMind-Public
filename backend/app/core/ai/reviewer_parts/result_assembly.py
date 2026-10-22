"""审查结果装配（输出契约瘦身）

职责: 把 LLM 的最小判断载荷与服务端权威字段装配为完整 ExtractedReviewResult
不做: 不调用 LLM、不访问 DB、不执行风险判断、不构造 Prompt、不做 Gate 判定

装配原则:
- 服务端已知的字段一律由 pair 侧权威数据填充，绝不采信模型回显
- 长度约束在此处确定性截断，不把纯长度问题变成整条 LLM 调用的失败
- 模型未提供的可选字段留 None，交由 result_builder 既有 fallback 处理
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.schemas.review import ExtractedReviewResult, ReviewJudgementPayload

if TYPE_CHECKING:
    from app.core.matcher.requirement_matcher import MatchedPair

# 与 ExtractedReviewResult 的 max_length 对齐，超出部分在边界截断
_REQUIREMENT_SUMMARY_LIMIT = 50
_RESPONSE_SUMMARY_LIMIT = 100


def _derive_is_compliant(
    compliance_level: str,
    model_value: Optional[bool],
) -> bool:
    """按符合程度推导二元合规标记。

    fully 与 not/missing 是确定的；partially 属灰区，优先沿用模型给定值以保持
    与旧契约一致的业务结果，模型未给时保守判为不符合。
    """
    if compliance_level == "fully":
        return True
    if compliance_level in ("not", "missing"):
        return False
    return bool(model_value) if model_value is not None else False


def assemble_review_result(
    payload: ReviewJudgementPayload,
    pair: "MatchedPair",
) -> ExtractedReviewResult:
    """用模型判断载荷与 pair 侧权威数据装配审查结果。"""
    requirement_description = (
        pair.requirement.description
        if pair.requirement and pair.requirement.description
        else ""
    )

    response_summary = payload.response_summary
    if not response_summary:
        response_content = (
            pair.response.content if pair.response and pair.response.content else ""
        )
        response_summary = response_content or "（模型未提供响应摘要）"

    return ExtractedReviewResult(
        # 服务端权威字段：不采信模型回显
        requirement_id=pair.requirement_id,
        bid_response_id=pair.response_id,
        category=pair.category,
        requirement_summary=requirement_description[:_REQUIREMENT_SUMMARY_LIMIT],
        response_summary=response_summary[:_RESPONSE_SUMMARY_LIMIT],
        # 模型判断字段
        compliance_level=payload.compliance_level,
        score=payload.score,
        comment=payload.comment,
        risk_level=payload.risk_level,
        is_compliant=_derive_is_compliant(payload.compliance_level, payload.is_compliant),
        # 模型可选字段：缺失留 None，由 result_builder 既有 fallback 兜底
        estimated_score=payload.estimated_score,
        rule_assessments=payload.rule_assessments,
        relevance_score=payload.relevance_score,
        relevance_note=payload.relevance_note,
    )
