"""审查 Prompt 发送前的输入闸门。

职责: 在调用 LLM 之前把 Prompt 收缩到硬上限内; 收缩到底仍超限时产出
 "证据包超限待判定"结果, 使单条款过载不外溢为整批审查中断。
不做: 不检索、不组装证据、不构造业务 Prompt 模板、不发起 LLM 调用、不判符合度。

背景: 证据过载此前只能靠 provider 返回超长 400 后被动收缩(input_guard),
对"排队/推理超时"类过载完全无效。本模块把闸门前移到发送之前。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

from app.core.ai.reviewer_parts.evidence_pack_budget import prompt_hard_cap_violation
from app.core.config import settings
from app.models.review_result import ReviewResult

if TYPE_CHECKING:
    from app.core.ai.reviewer import ReviewOrchestrator
    from app.core.matcher.requirement_matcher import MatchedPair


def fit_prompt_within_hard_cap(
    prompt: str,
    rebuild: Callable[[int], str],
) -> tuple[str, Optional[str]]:
    """在本地把 prompt 收缩到硬上限内; 收缩到底仍超限则返回原因。

    Args:
        prompt: round 0 已组装好的 Prompt
        rebuild: 按 reduction_round 重建 Prompt 的回调（骨架不动）

    Returns:
        (最终 prompt, 超限原因); 原因为 None 表示可以安全发送。
    """
    from app.core.ai.reviewer import _get_review_prompts

    try:
        system_message = _get_review_prompts()[0]
    except Exception:  # noqa: BLE001 预检不得因取模板失败而阻断审查
        system_message = ""

    reason = prompt_hard_cap_violation(system_message, prompt)
    if reason is None:
        return prompt, None

    shrink_rounds = max(0, int(settings.REVIEW_INPUT_SHRINK_RETRIES))
    for reduction_round in range(1, shrink_rounds + 1):
        try:
            candidate = rebuild(reduction_round)
        except Exception as err:  # noqa: BLE001 重建失败按仍超限处置
            logger.warning(f"证据包收缩重建失败(round={reduction_round}): {err}")
            break
        reason = prompt_hard_cap_violation(system_message, candidate)
        if reason is None:
            logger.info(
                f"证据包超限已在本地收缩至可发送: reduction_round={reduction_round}"
            )
            return candidate, None
        prompt = candidate
    return prompt, reason


def build_oversize_pending_result(
    self: "ReviewOrchestrator",
    project_id: int,
    pair: "MatchedPair",
    reason: str,
) -> ReviewResult:
    """证据包超出输入预算时的待判定结果（不调用 LLM, 不影响其余条款）。

 复用 既有映射: 带 ``LLM_REVIEW_FAILURE`` 标记的结果在结果 API 出口
 被翻译为 ``pending_judgment``, 不新增第五种符合程度, 也不构成未响应推定。
 """
    from app.core.ai.reviewer_parts.result_builder import FAILURE_COMMENT_MARKER

    result = self._create_failure_result(project_id, pair)
    result.comment = (
        f"审查失败[{FAILURE_COMMENT_MARKER}]：证据包超出模型输入预算（{reason}），"
        f"已跳过自动审查，请人工复核"
    )
    result.reconcile_note = "证据包超限待判定，未执行 AI 审查，不构成未响应推定"
    result.consistency_flag = "needs_review"
    logger.warning(f"要求 {pair.requirement_id}: 证据包超限转人工复核({reason})")
    return result


__all__ = ["build_oversize_pending_result", "fit_prompt_within_hard_cap"]
