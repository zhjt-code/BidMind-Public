"""Judge 抽样接入（B5.12）。

按 10% 常规抽样 + 100% 高风险触发 Judge 复核。
三流程统一调用入口。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.ai.critic.flows import CriticFlow
from app.core.ai.critic.judge import (
    JudgeResult,
    run_judge,
    should_sample,
)

logger = logging.getLogger(__name__)


async def maybe_run_judge(
    project_id: int | str | None,
    original_output: dict[str, Any],
    context_summary: str,
    flow: CriticFlow | str,
    is_high_risk: bool = False,
    *,
    sample_key: str | int | None = None,
    evidence: dict[str, Any] | None = None,
) -> JudgeResult | None:
    """Conditionally invoke Judge based on sampling logic.

    Args:
        sample_key: 条目级采样键（``project_id:requirement_id`` /
            ``project_id:pair_label`` / ``project_id:node_id`` 等）。
            省略时回退到 project_id，保持向后兼容。
        evidence: P1.2 新增。writer/review/collusion 可携带业务证据
            (bound_materials / hard_constraints / snippets 等), 由
            run_judge 渲染到 prompt 的 {evidence_text} 占位符。

    Returns:
        ``JudgeResult`` if sampled, ``None`` otherwise.
    """
    if not should_sample(
        project_id, is_high_risk=is_high_risk, sample_key=sample_key,
    ):
        return None

    try:
        from app.core.ai.ai_factory import get_judge_client
        client = get_judge_client(project_id=int(project_id) if project_id else None)
    except Exception as e:
        logger.warning("Failed to get judge client: %s", e)
        return JudgeResult(verdict="skipped", confidence=0.0, reason=f"client error: {e}")

    return await run_judge(
        original_output=original_output,
        context_summary=context_summary,
        llm_client=client,
        flow=flow,
        evidence=evidence,
    )
