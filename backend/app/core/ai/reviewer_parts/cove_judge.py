"""CoVe 自我验证 + Judge investigate（R2-3 Step 5 抽出）

涵盖以下原 `ReviewOrchestrator` 方法：

- `maybe_run_cove_investigate` (B5.10): 否决项 not + mandatory 触发 CoVe，不改判
- `maybe_run_judge_investigate` (B5.12): 10% 抽样 + 100% 否决项 not 触发 Judge

两者均为 advisory 链路：CoVe disagree 只把 failure_state 设为 needs_review，
Judge investigate 仅采集指标不修改结论，与 reviewer 主判流程解耦。

签名规范: 子模块函数去前导下划线, 第一参数保留 `self`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from app.core.ai.critic.flows import FLOW_INVESTIGATE
from app.core.matcher.requirement_matcher import MatchedPair
from app.core.review_contracts import parse_page_numbers
from app.models.review_result import ReviewResult

if TYPE_CHECKING:
    from app.core.ai.reviewer import ReviewOrchestrator
    from app.schemas.review import ExtractedReviewResult


# ---------------------------------------------------------------------------
# B5.10 CoVe (Chain-of-Verification) for investigate flow
# ---------------------------------------------------------------------------


async def maybe_run_cove_investigate(
    self: "ReviewOrchestrator",
    project_id: int,
    pair: MatchedPair,
    extracted: "ExtractedReviewResult",
    review_result: ReviewResult,
) -> None:
    """B5.10 投标 CoVe: 否决项 not + mandatory 触发。

    Feature flag: COVE_ENABLED / COVE_RATIO (via should_use_cove).
    CoVe 不改判，只在 disagree 时推入 needs_review。

    critic-quality-fix：
    - 传入结构化 ``evidence_snippets``（含 source_pages / 响应内容 / evidence_anchor）,
      使 CoVe prompt 真正看到原文，而不是只看到关键词。
    - 原因（reason）透传到 failure_reason，便于定位 inconclusive 类型。
    """
    try:
        from app.core.ai.critic.cove import run_cove, should_use_cove
    except Exception:
        return

    if not should_use_cove(project_id):
        return

    _cl = (extracted.compliance_level or "").lower()
    if _cl != "not":
        return
    if not pair.is_mandatory:
        return

    try:
        from app.core.ai.ai_factory import get_judge_client
        _client = get_judge_client(project_id=project_id)
        if _client is None:
            return

        _response_content = ""
        if pair.response and pair.response.content:
            _response_content = pair.response.content
        _response_pages = parse_page_numbers(
            pair.response.source_pages if pair.response else None
        )
        _primary_page = _response_pages[0] if _response_pages else None
        _evidence_anchor = ""
        if pair.response and getattr(pair.response, "evidence_anchor", None):
            _evidence_anchor = pair.response.evidence_anchor or ""

        evidence_snippets: list[dict] = []
        if _response_content:
            evidence_snippets.append({
                "page": _primary_page,
                "text": _response_content,
            })
        if _evidence_anchor and _evidence_anchor not in _response_content:
            evidence_snippets.append({
                "page": _primary_page,
                "text": _evidence_anchor,
            })

        _chunks = [_response_content] if _response_content else []
        _clause_kw = (pair.requirement.description or "")[:200] if pair.requirement else ""
        _mandatory_val = (extracted.comment or "")[:200]
        _chunk_page_label = (
            ",".join(str(p) for p in _response_pages)
            if _response_pages else "N/A"
        )

        _result = await run_cove(
            trigger="investigate_not_mandatory",
            template_vars={
                "clause_keyword": _clause_kw,
                "mandatory_value": _mandatory_val,
                "chunk_page": _chunk_page_label,
            },
            chunks=_chunks,
            llm_client=_client,
            flow=FLOW_INVESTIGATE,
            evidence_snippets=evidence_snippets or None,
        )
        if _result.should_escalate:
            if hasattr(review_result, "failure_state"):
                review_result.failure_state = "needs_review"
            if hasattr(review_result, "failure_reason"):
                _tag = _result.outcome
                if _result.reason:
                    _tag = f"{_tag}/{_result.reason}"
                review_result.failure_reason = f"CoVe disagree: {_tag}"
    except Exception as e:
        logger.debug(f"CoVe investigate degraded: {e}")


# ---------------------------------------------------------------------------
# B5.12 Judge investigate (10% + 100% 否决项 not+mandatory)
# ---------------------------------------------------------------------------


async def maybe_run_judge_investigate(
    self: "ReviewOrchestrator",
    project_id: int,
    pair: MatchedPair,
    extracted: "ExtractedReviewResult",
    review_result: ReviewResult,
) -> None:
    """B5.12 投标 Judge 抽样 (10% + 100% 否决项 not+mandatory).

    critic-quality-fix：
    - context_summary 升级为结构化文本：招标要求、投标响应摘要、
      source_pages、evidence_anchor、主判 comment，降低"空上下文"
      带来的假 disagree。
    - sample_key 使用 ``project_id:requirement_id``，由项目级 hash
      改为条目级 hash。
    """
    try:
        from app.core.ai.critic.judge_sampler import maybe_run_judge
    except Exception:
        return

    try:
        _cl = (extracted.compliance_level or "").lower()
        _is_high = (_cl == "not" and bool(pair.is_mandatory))

        _req_desc = (pair.requirement.description or "") if pair.requirement else ""
        _resp_content = ""
        _source_pages_label = ""
        _evidence_anchor = ""
        if pair.response:
            _resp_content = pair.response.content or ""
            pages_list = parse_page_numbers(pair.response.source_pages)
            _source_pages_label = ",".join(str(p) for p in pages_list)
            _evidence_anchor = getattr(pair.response, "evidence_anchor", "") or ""

        _main_comment = (extracted.comment or "")

        context_lines = [
            f"招标要求(ID={pair.requirement_id}): {_req_desc[:400]}",
            f"是否否决项: {'是' if pair.is_mandatory else '否'}",
            f"投标响应摘要: {(_resp_content[:500] or '（无响应内容）')}",
        ]
        if _source_pages_label:
            context_lines.append(f"响应来源页: {_source_pages_label}")
        if _evidence_anchor:
            context_lines.append(f"证据锚点: {_evidence_anchor[:200]}")
        if _main_comment:
            context_lines.append(f"主判审查意见: {_main_comment[:500]}")

        await maybe_run_judge(
            project_id=project_id,
            original_output={
                "compliance_level": extracted.compliance_level,
                "risk_level": extracted.risk_level,
                "score": extracted.score,
                "is_mandatory": bool(pair.is_mandatory),
                "comment": _main_comment[:500],
            },
            context_summary="\n".join(context_lines),
            flow=FLOW_INVESTIGATE,
            is_high_risk=_is_high,
            sample_key=f"{project_id}:{pair.requirement_id}",
        )
    except Exception as e:
        logger.debug(f"Judge investigate degraded: {e}")
