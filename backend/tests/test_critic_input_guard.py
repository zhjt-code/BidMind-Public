"""CoVe/Judge 输入超长 400 兜底防御测试。

区别于 test_cove_truncation_retry(覆盖输出侧截断): 本测试覆盖输入侧超长 400 的
语义收缩重试与 fail-safe, 确保:
- 命中输入过长 400 时收缩证据后重试成功(judge 断言 evidence 确实变短);
- 收缩到下限仍 400 时保持各自 fail-safe(cove→inconclusive, judge→skipped), 不崩、不静默放行。
"""

import asyncio
import sys
from pathlib import Path

import httpx
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.critic.cove import CoveResult, run_cove
from app.core.ai.critic.judge import JudgeResult, run_judge


def _make_api_status_error(
    message: str = "Range of input length should be [1, 30720]",
) -> APIStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status_code=400, request=request)
    return APIStatusError(message, response=response, body={"error": {"message": message}})


_COVE_TEMPLATE_VARS = {"clause_keyword": "demo", "mandatory_value": "demo", "chunk_page": "1"}
_VALID_ANSWER = {"answer": "yes", "evidence_page": 1, "quote": "demo quote"}
_VALID_VERDICT = {"verdict": "agree", "confidence": 0.9, "reason": "ok"}


class _ScriptedClient:
    """behavior(call_index) 返回 dict 作为响应, 返回/抛 Exception 则抛出。"""

    def __init__(self, behavior):
        self.calls: list[dict] = []
        self._behavior = behavior

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self._behavior(len(self.calls))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# ------------------------------- CoVe -------------------------------


def test_cove_recovers_after_input_too_long_400():
    """首问输入超长 400 → 收缩证据重试成功, answer 正常参与多数决。"""
    client = _ScriptedClient(
        lambda i: _make_api_status_error() if i == 1 else dict(_VALID_ANSWER)
    )
    result = asyncio.run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars=_COVE_TEMPLATE_VARS,
        chunks=["demo quote"],
        llm_client=client,
        flow="investigate",
    ))

    assert isinstance(result, CoveResult)
    # 首问重试一次(2) + 后两问各一次 = 4
    assert len(client.calls) == 4
    assert result.outcome != "skipped"  # 未因异常被跳过


def test_cove_fail_safe_when_input_too_long_persists():
    """证据收缩到下限仍 400 → 每问 invalid → fail-safe inconclusive, 不升级为 disagree。"""
    client = _ScriptedClient(lambda i: _make_api_status_error())
    result = asyncio.run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars=_COVE_TEMPLATE_VARS,
        chunks=["demo quote"],
        llm_client=client,
        flow="investigate",
    ))

    assert isinstance(result, CoveResult)
    assert result.outcome == "inconclusive"
    assert result.should_escalate is False  # 输入故障不得被误判为 disagree 升级


# ------------------------------- Judge -------------------------------


def _big_evidence() -> dict:
    """构造超出 round1 预算(10000)的大证据, 以便观测收缩生效。"""
    return {"bound_materials": [{"title": f"m{i}", "snippet": "x" * 400} for i in range(60)]}


def test_judge_recovers_and_shrinks_evidence_after_input_too_long_400():
    """首次输入超长 400 → 收缩 evidence 重试成功, 第二次 user_message 明显变短。"""
    client = _ScriptedClient(
        lambda i: _make_api_status_error() if i == 1 else dict(_VALID_VERDICT)
    )
    result = asyncio.run(run_judge(
        original_output={"score": 80},
        context_summary="requirement 1: demo",
        llm_client=client,
        flow="writer",
        evidence=_big_evidence(),
    ))

    assert isinstance(result, JudgeResult)
    assert result.verdict == "agree"
    assert len(client.calls) == 2
    first_len = len(client.calls[0]["user_message"])
    second_len = len(client.calls[1]["user_message"])
    assert second_len < first_len  # 第二次证据被语义收缩, 骨架(original_output/schema)仍在


def test_judge_fail_safe_when_input_too_long_persists():
    """收缩到下限仍 400 → fail-safe skipped(不崩, 不静默 agree/disagree)。"""
    client = _ScriptedClient(lambda i: _make_api_status_error())
    result = asyncio.run(run_judge(
        original_output={"score": 80},
        context_summary="demo",
        llm_client=client,
        flow="writer",
        evidence=_big_evidence(),
    ))

    assert isinstance(result, JudgeResult)
    assert result.verdict == "skipped"
