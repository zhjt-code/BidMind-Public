"""Judge / CoVe `response_format` 契约回归测试。

目的: 防止再次把裸 JSON Schema (根节点 type="object") 直接传给
`CloudClient.chat_json` 的 `json_schema` 参数 -- 实际会作为
`response_format` 下发, 在 deepseek/qwen 等 provider 上会返回
400 `response_format.type: unknown variant 'object'`。

断言的契约 (S.3.4 后更新):
- `run_judge()` 传递的 `json_schema` 形态可以是 `{"type": "json_object"}` (降级)
  或 `{"type": "json_schema", "json_schema": {strict schema}}` (升级),
  **绝不可以是 `{"type": "object", ...}` 的裸 JSON Schema**
- `run_cove()` 当前仍用 `{"type": "json_object"}`
- 异常路径仍保持 fail-safe: judge -> `skipped`, cove answer -> `invalid`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.ai.critic.cove import CoveResult, run_cove
from app.core.ai.critic.judge import (
    JUDGE_VERDICT_RESPONSE_FORMAT,
    JUDGE_VERDICT_SCHEMA,
    JudgeResult,
    run_judge,
)
from app.core.ai.critic.prompts.cove_templates import (
    ANSWER_JSON_SCHEMA,
    ANSWER_RESPONSE_FORMAT,
)


class _RecordingClient:
    """记录 chat_json 调用入参的最小化假客户端。"""

    def __init__(self, response: dict[str, Any] | None = None,
                 raise_on_call: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or {
            "verdict": "agree",
            "confidence": 0.9,
            "reason": "ok",
        }
        self._raise_on_call = raise_on_call

    async def chat_json(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raise_on_call:
            raise RuntimeError("simulated provider error")
        return self._response


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 常量形态契约
# ---------------------------------------------------------------------------


def test_judge_response_format_is_not_bare_schema() -> None:
    """S.3.4 后: Judge 升 strict json_schema。允许 json_object（降级路径）或
    json_schema（strict），但绝不能是裸 JSON Schema。"""
    rf_type = JUDGE_VERDICT_RESPONSE_FORMAT.get("type")
    assert rf_type in ("json_object", "json_schema"), (
        f"意外的 Judge response_format 形态: {JUDGE_VERDICT_RESPONSE_FORMAT}"
    )
    # 核心防御: 根节点绝不能是裸 schema 的 object
    assert rf_type != "object"


def test_cove_response_format_is_json_object() -> None:
    assert ANSWER_RESPONSE_FORMAT == {"type": "json_object"}


def test_judge_bare_schema_kept_for_reference_only() -> None:
    # 裸 schema 仍保留为文档/本地校验参考, 但根节点 type 必然是 "object",
    # 因此绝不可直接作为 response_format 下发。
    assert JUDGE_VERDICT_SCHEMA["type"] == "object"


def test_cove_bare_schema_kept_for_reference_only() -> None:
    assert ANSWER_JSON_SCHEMA["type"] == "object"


# ---------------------------------------------------------------------------
# 运行时调用契约
# ---------------------------------------------------------------------------


def test_run_judge_passes_safe_response_format() -> None:
    """S.3.4: Judge 透传 strict json_schema 或降级到 json_object，
    但绝不能传裸 JSON Schema（根 type=object）。"""
    client = _RecordingClient(
        response={"verdict": "agree", "confidence": 0.8, "reason": "ok"}
    )

    result = _run(run_judge(
        original_output={"score": 80},
        context_summary="requirement 1: demo",
        llm_client=client,
        flow="investigate",
    ))

    assert isinstance(result, JudgeResult)
    assert result.verdict == "agree"
    assert len(client.calls) == 1
    rf = client.calls[0]["json_schema"]
    assert rf.get("type") in ("json_object", "json_schema"), (
        f"Judge 传出了非法 response_format: {rf}"
    )
    # 防御: 不应把裸 JSON Schema (根 type=object) 当 response_format 传出
    assert rf.get("type") != "object"


def test_run_judge_fail_safe_on_client_error() -> None:
    client = _RecordingClient(raise_on_call=True)

    result = _run(run_judge(
        original_output={"score": 80},
        context_summary="requirement 1: demo",
        llm_client=client,
        flow="investigate",
    ))

    assert isinstance(result, JudgeResult)
    assert result.verdict == "skipped"
    assert result.confidence == 0.0
    assert "judge error" in result.reason
    # 即使失败也必须使用兼容的 response_format 形态（S.3.4 后允许 json_schema 或 json_object）
    rf = client.calls[0]["json_schema"]
    assert rf.get("type") in ("json_object", "json_schema")
    assert rf.get("type") != "object"


def test_run_cove_passes_json_object_response_format_for_each_question() -> None:
    client = _RecordingClient(response={
        "answer": "yes",
        "evidence_page": 1,
        "quote": "demo quote",
    })

    result = _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "demo",
            "mandatory_value": "demo",
            "chunk_page": "1",
        },
        chunks=["demo quote"],
        llm_client=client,
        flow="investigate",
    ))

    assert isinstance(result, CoveResult)
    assert result.trigger == "investigate_not_mandatory"
    assert len(client.calls) >= 1
    for call in client.calls:
        assert call["json_schema"] == {"type": "json_object"}
        assert call["json_schema"].get("type") != "object"


def test_run_cove_fail_safe_on_client_error() -> None:
    client = _RecordingClient(raise_on_call=True)

    result = _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "demo",
            "mandatory_value": "demo",
            "chunk_page": "1",
        },
        chunks=["demo quote"],
        llm_client=client,
        flow="investigate",
    ))

    assert isinstance(result, CoveResult)
    assert len(result.answers) >= 1
    for ans in result.answers:
        assert ans.answer == "invalid"
        assert ans.valid is False
    # 即便每题失败, 传出的 json_schema 仍必须是兼容形态
    for call in client.calls:
        assert call["json_schema"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# CloudClient.chat_json 参数语义提醒
# ---------------------------------------------------------------------------


def test_chat_json_param_is_response_format_not_bare_schema() -> None:
    """锁定 CloudClient._chat_with_response_format 的 json_schema 语义是"完整 response_format"。

    只做字符串层面的契约断言, 防止日后被改成"裸 JSON Schema"的语义。
    """
    from app.core.ai.cloud_client import CloudClient

    doc = CloudClient._chat_with_response_format.__doc__ or ""
    assert "response_format" in doc


# ---------------------------------------------------------------------------
# qwen json_object 内容约束: messages 必须包含 'json' 字样
# ---------------------------------------------------------------------------


def test_run_judge_prompt_contains_json_keyword() -> None:
    """qwen json_object 要求 messages 合并文本必须含 'json'; 缺失会 400."""
    client = _RecordingClient(
        response={"verdict": "agree", "confidence": 0.8, "reason": "ok"}
    )

    _run(run_judge(
        original_output={"score": 80},
        context_summary="requirement 1: demo",
        llm_client=client,
        flow="investigate",
    ))

    merged = (client.calls[0].get("system_message") or "") \
        + "\n" + (client.calls[0].get("user_message") or "")
    assert "json" in merged.lower()


def test_run_cove_prompt_contains_json_keyword() -> None:
    client = _RecordingClient(response={
        "answer": "yes",
        "evidence_page": 1,
        "quote": "demo quote",
    })

    _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "demo",
            "mandatory_value": "demo",
            "chunk_page": "1",
        },
        chunks=["demo quote"],
        llm_client=client,
        flow="investigate",
    ))

    # 每道问题合并文本都必须含 'json'
    for call in client.calls:
        merged = (call.get("system_message") or "") \
            + "\n" + (call.get("user_message") or "")
        assert "json" in merged.lower(), f"prompt 缺 'json' 字样: {merged[:120]}"
