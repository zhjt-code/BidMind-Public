""" 防御测试: CoVe/Judge token 预算与截断扩容重试。

真实风险: gemini-3.5-flash 等思考型模型的 max_tokens 预算包含思考 token,
CoVe 旧预算 500 时正文 JSON 开头即被截断 (doc 形如 '{\\n "answer": "'),
三问全部 invalid -> 多数决恒为 inconclusive, CoVe 复核层静默失效。

断言契约:
1. CoVe 首次调用预算 >= 2000, 预算回退视为回归
2. 截断型 JSONDecodeError 触发且仅触发一次扩容重试 (x2, 封顶 4096)
3. 扩容重试成功后答案正常参与多数决
4. 重试仍截断 -> fail-safe answer=invalid, 单题不超过 2 次调用
5. 非截断型 JSONDecodeError 不触发扩容重试
6. Judge 各 flow 预算 >= 1500, writer flow >= 2000
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from app.core.ai.critic.cove import (
    COVE_MAX_TOKENS,
    COVE_TOKEN_CEILING,
    CoveResult,
    run_cove,
)
from app.core.ai.critic.judge import (
    _DEFAULT_MAX_TOKENS,
    _MAX_TOKENS_BY_FLOW,
    JudgeResult,
    run_judge,
)

_COVE_TEMPLATE_VARS = {
    "clause_keyword": "demo",
    "mandatory_value": "demo",
    "chunk_page": "1",
}

_VALID_ANSWER = {"answer": "yes", "evidence_page": 1, "quote": "demo quote"}


def _truncated_json_error() -> json.JSONDecodeError:
    """复刻线上特征: 思考耗尽预算后正文在字符串中途截断。"""
    return json.JSONDecodeError(
        "Unterminated string starting at", '{\n  "answer": "', 14,
    )


def _non_truncation_json_error() -> json.JSONDecodeError:
    """非截断型语义错误: 完整 JSON 后跟多余内容。"""
    return json.JSONDecodeError("Extra data", '{"answer": "yes"} extra', 18)


class _ScriptedClient:
    """按调用序号决定行为的假客户端。

    behavior(call_index) 返回 dict 则作为响应, 返回 Exception 则抛出。
    call_index 从 1 开始计数。
    """

    def __init__(self, behavior: Callable[[int], Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._behavior = behavior

    async def chat_json(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self._behavior(len(self.calls))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run_cove_with(client: _ScriptedClient) -> CoveResult:
    return asyncio.run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars=_COVE_TEMPLATE_VARS,
        chunks=["demo quote"],
        llm_client=client,
        flow="investigate",
    ))


# ---------------------------------------------------------------------------
# 预算契约
# ---------------------------------------------------------------------------


def test_cove_budget_covers_thinking_models() -> None:
    """预算常量回归护栏: 低于 2000 会在思考型模型上重现。"""
    assert COVE_MAX_TOKENS >= 2000
    assert COVE_TOKEN_CEILING >= COVE_MAX_TOKENS * 2


def test_cove_first_attempt_uses_full_budget() -> None:
    client = _ScriptedClient(lambda i: dict(_VALID_ANSWER))

    result = _run_cove_with(client)

    assert isinstance(result, CoveResult)
    assert len(client.calls) == 3  # 三问, 每问一次, 无重试
    for call in client.calls:
        assert call["max_tokens"] == COVE_MAX_TOKENS


def test_judge_flow_budgets_cover_thinking_models() -> None:
    assert _DEFAULT_MAX_TOKENS >= 1500
    for flow, budget in _MAX_TOKENS_BY_FLOW.items():
        assert budget >= 1500, f"flow={flow} 预算 {budget} 低于思考型模型安全线"
    from app.core.ai.critic.flows import FLOW_WRITER
    assert _MAX_TOKENS_BY_FLOW[FLOW_WRITER] >= 2000


def test_run_judge_passes_raised_budget() -> None:
    client = _ScriptedClient(
        lambda i: {"verdict": "agree", "confidence": 0.9, "reason": "ok"},
    )

    result = asyncio.run(run_judge(
        original_output={"score": 80},
        context_summary="requirement 1: demo",
        llm_client=client,
        flow="investigate",
    ))

    assert isinstance(result, JudgeResult)
    assert len(client.calls) == 1
    assert client.calls[0]["max_tokens"] >= 1500


# ---------------------------------------------------------------------------
# 截断扩容重试
# ---------------------------------------------------------------------------


def test_truncation_triggers_single_expanded_retry_and_recovers() -> None:
    """每问首次截断、重试成功: 答案应正常参与多数决得出 agree。"""
    # 奇数次调用(每问第一次)截断, 偶数次调用(重试)成功
    client = _ScriptedClient(
        lambda i: _truncated_json_error() if i % 2 == 1 else dict(_VALID_ANSWER),
    )

    result = _run_cove_with(client)

    assert len(client.calls) == 6  # 三问 x (首次 + 重试)
    for idx, call in enumerate(client.calls, start=1):
        if idx % 2 == 1:
            assert call["max_tokens"] == COVE_MAX_TOKENS
        else:
            assert call["max_tokens"] == min(
                COVE_MAX_TOKENS * 2, COVE_TOKEN_CEILING,
            )
    assert result.outcome == "agree"
    assert all(a.answer == "yes" and a.valid for a in result.answers)


def test_retry_still_truncated_fails_safe_without_third_call() -> None:
    """重试仍截断: 单题恰好 2 次调用, 全部 invalid, 多数决 inconclusive。"""
    client = _ScriptedClient(lambda i: _truncated_json_error())

    result = _run_cove_with(client)

    assert len(client.calls) == 6  # 每题严格 2 次, 无第三次
    assert all(a.answer == "invalid" and not a.valid for a in result.answers)
    assert result.outcome == "inconclusive"
    assert result.reason == "no_valid_quote"
    assert result.should_escalate is False


def test_non_truncation_json_error_does_not_retry() -> None:
    """非截断型 JSONDecodeError 不应消耗扩容重试预算。"""
    client = _ScriptedClient(lambda i: _non_truncation_json_error())

    result = _run_cove_with(client)

    assert len(client.calls) == 3  # 每题仅 1 次, 无重试
    assert all(a.answer == "invalid" for a in result.answers)


def test_non_json_exception_does_not_retry() -> None:
    """provider 级异常(非 JSONDecodeError)保持原 fail-safe 路径, 不重试。"""
    client = _ScriptedClient(lambda i: RuntimeError("simulated provider error"))

    result = _run_cove_with(client)

    assert len(client.calls) == 3
    assert all(a.answer == "invalid" for a in result.answers)
