"""P0-D: 统一输出合同与失败状态分离 防御测试。

锁定契约:
- allow_plain_fallback=False 时, 全通道不支持 response_format 必须抛
  UnsupportedResponseFormatError, 不产生普通 chat 降级产物;
- 默认(True)保持既有降级行为, 且降级触发 Prometheus fallback 计数;
- CloudClient 直连路径同样支持 fail-closed;
- Judge 在 fail-closed 异常下走 skipped fail-safe, 不接受降级产物;
- 提取失败信号(ExtractionFailedSignal)与业务 not-found 分离:
  落库标记为 [提取失败待复核] 而非 [未找到响应];
- 审查侧对提取失败项直接产出人工复核结论, 不进入"未响应=不符合"推定。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.core.ai.cloud_client import CloudClient, UnsupportedResponseFormatError
from app.core.ai.dispatcher import AIDispatcher
from app.core.extractor.extraction_failure import (
    EXTRACTION_FAILED_CONTENT,
    ExtractionFailedSignal,
    build_extraction_failed_response,
    is_extraction_failed_content,
)


class _FakeClient:
    def __init__(self, model_name: str, json_behavior: Any, chat_result: str = "PLAIN") -> None:
        self.base_url = "https://example.com/v1"
        self.model_name = model_name
        self._json_behavior = json_behavior
        self._chat_result = chat_result
        self.json_calls = 0
        self.chat_calls = 0
        self.last_json_kwargs: dict = {}

    async def chat_json(self, **kwargs: Any) -> Any:
        self.json_calls += 1
        self.last_json_kwargs = kwargs
        if isinstance(self._json_behavior, Exception):
            # 模拟 CloudClient 契约: propagate_unsupported=False 时内部回退普通 chat
            if (
                isinstance(self._json_behavior, UnsupportedResponseFormatError)
                and kwargs.get("propagate_unsupported") is False
            ):
                self.chat_calls += 1
                import json as _json
                return _json.loads(self._chat_result)
            raise self._json_behavior
        return self._json_behavior

    async def chat(self, **kwargs: Any) -> Any:
        self.chat_calls += 1
        return self._chat_result


# ---------- Dispatcher fail-closed ----------

@pytest.mark.asyncio
async def test_fail_closed_raises_without_plain_fallback() -> None:
    dispatcher = AIDispatcher(purpose="judge")
    a1 = _FakeClient("m1", UnsupportedResponseFormatError("rf unsupported"))
    b1 = _FakeClient("m2", UnsupportedResponseFormatError("rf unsupported"))
    dispatcher.register_channel(a1, config_id="cfgA", key_index=0, model_group="gA", priority=0)
    dispatcher.register_channel(b1, config_id="cfgB", key_index=0, model_group="gB", priority=100)

    with pytest.raises(UnsupportedResponseFormatError):
        await dispatcher.chat_json(
            user_message="json please",
            json_schema={"type": "json_schema"},
            allow_plain_fallback=False,
        )

    assert a1.chat_calls == 0 and b1.chat_calls == 0, (
        "fail-closed 下不得产生任何普通 chat 降级调用"
    )


@pytest.mark.asyncio
async def test_default_fallback_preserved_and_counted() -> None:
    """对照组: 默认 allow_plain_fallback=True 时降级行为保持且被计数"""
    dispatcher = AIDispatcher(purpose="judge")
    a1 = _FakeClient(
        "m1", UnsupportedResponseFormatError("rf unsupported"),
        chat_result='{"verdict": "agree"}',
    )
    dispatcher.register_channel(a1, config_id="cfgA", key_index=0, model_group="gA", priority=0)

    with patch("app.core.ai.dispatcher.fallback_trigger_total") as mock_counter:
        result = await dispatcher.chat_json(
            user_message="json please",
            json_schema={"type": "json_schema"},
        )

    assert result == {"verdict": "agree"}
    assert a1.json_calls >= 1
    triggers = [
        c.kwargs.get("trigger_type") or (c.args[0] if c.args else None)
        for c in mock_counter.labels.call_args_list
    ]
    assert "plain_chat_degradation" in triggers, "降级必须触发 Prometheus 计数"


@pytest.mark.asyncio
async def test_allow_plain_fallback_not_forwarded_to_channel() -> None:
    """allow_plain_fallback 是 dispatcher 语义, 不得下发给通道方法"""
    dispatcher = AIDispatcher(purpose="judge")
    ok = _FakeClient("m1", {"ok": True})
    dispatcher.register_channel(ok, config_id="cfgA", key_index=0, model_group="gA", priority=0)

    result = await dispatcher.chat_json(
        user_message="json please",
        json_schema={"type": "json_schema"},
        allow_plain_fallback=False,
    )
    assert result == {"ok": True}
    assert "allow_plain_fallback" not in ok.last_json_kwargs


# ---------- CloudClient 直连 fail-closed ----------

def test_cloud_client_fail_closed_no_silent_fallback() -> None:
    client = CloudClient(
        base_url="https://demo.example.com/v1", api_key="sk-test",
        model_name="demo", timeout=30.0,
    )
    with patch.object(
        client, "_chat_with_response_format",
        new=AsyncMock(side_effect=UnsupportedResponseFormatError("nope")),
    ), patch.object(client, "chat", new=AsyncMock(return_value="PLAIN")) as mock_chat:
        with pytest.raises(UnsupportedResponseFormatError):
            asyncio.run(client.chat_json(
                user_message="json",
                json_schema={"type": "json_schema"},
                allow_plain_fallback=False,
            ))
        mock_chat.assert_not_called()


# ---------- Judge fail-safe ----------

def test_judge_skips_on_fail_closed_exception() -> None:
    from app.core.ai.critic.judge import run_judge

    class _JudgeClient:
        def __init__(self) -> None:
            self.seen_kwargs: dict = {}

        async def chat_json(self, **kwargs: Any) -> Any:
            self.seen_kwargs = kwargs
            raise UnsupportedResponseFormatError("all candidates unsupported")

    client = _JudgeClient()
    result = asyncio.run(run_judge(
        original_output={"is_compliant": True},
        context_summary="ctx",
        llm_client=client,
    ))
    assert client.seen_kwargs.get("allow_plain_fallback") is False, (
        "Judge 必须声明 fail-closed"
    )
    assert result.verdict == "skipped", "fail-closed 异常必须走 skipped fail-safe"


# ---------- 提取失败与业务缺失分离 ----------

def test_extraction_failed_signal_flows_as_not_found_shape() -> None:
    signal = ExtractionFailedSignal(reason="timeout")
    assert signal.found is False, "信号必须能安全流经 `not extracted.found` 分支"


def test_failed_response_marker_distinct_from_not_found() -> None:
    response = build_extraction_failed_response(101, 5, reason="quota_exhausted")
    assert response.content == EXTRACTION_FAILED_CONTENT
    assert response.content != "[未找到响应]", "模型故障不得伪装成业务缺失"
    assert "quota_exhausted" in (response.relevance_note or "")
    assert is_extraction_failed_content(response.content) is True
    assert is_extraction_failed_content("[未找到响应]") is False


def test_matched_pair_treats_failed_extraction_as_unconfirmed() -> None:
    """提取失败/证据不足/未找到均视为未确认占位, 不得当作已找到响应。"""
    from app.core.extractor.evidence_verdict import (
        EVIDENCE_INSUFFICIENT_CONTENT,
        is_unconfirmed_response_content,
    )
    from app.core.matcher.requirement_matcher import MatchedPair
    from app.models.requirement import Requirement
    from app.models.bid_response import BidResponse

    req = Requirement(id=1, project_id=1, category="qualification", description="资质要求")
    failed = BidResponse(
        requirement_id=1, document_id=2, content=EXTRACTION_FAILED_CONTENT,
    )
    not_found = BidResponse(
        requirement_id=1, document_id=2, content="[未找到响应]",
    )
    insufficient = BidResponse(
        requirement_id=1, document_id=2, content=EVIDENCE_INSUFFICIENT_CONTENT,
    )
    assert is_unconfirmed_response_content(failed.content)
    assert MatchedPair(requirement=req, response=failed).has_response is False
    assert MatchedPair(requirement=req, response=not_found).has_response is False
    assert MatchedPair(requirement=req, response=insufficient).has_response is False


def test_reviewer_short_circuits_failed_extraction_to_manual_review() -> None:
    """审查侧对提取失败项直接产出人工复核结论, 不调用 LLM"""
    from app.core.ai.reviewer_parts.pair_review import review_single_pair
    from app.core.matcher.requirement_matcher import MatchedPair
    from app.models.requirement import Requirement
    from app.models.bid_response import BidResponse

    req = Requirement(
        id=7, project_id=1, category="qualification",
        description="须具备建筑工程施工总承包一级资质", is_mandatory=True,
    )
    failed = BidResponse(requirement_id=7, document_id=2, content=EXTRACTION_FAILED_CONTENT)
    pair = MatchedPair(requirement=req, response=failed)

    class _Result:
        comment = ""
        reconcile_note = ""
        consistency_flag = ""

    class _OrchestratorStub:
        def __init__(self) -> None:
            self.llm_prompt_built = False

        def _create_failure_result(self, project_id: int, p: Any) -> _Result:
            return _Result()

        def _build_review_prompt(self, *args: Any, **kwargs: Any) -> str:
            self.llm_prompt_built = True
            return ""

        def _build_no_response_prompt(self, *args: Any, **kwargs: Any) -> str:
            self.llm_prompt_built = True
            return ""

    stub = _OrchestratorStub()
    result = asyncio.run(review_single_pair(stub, project_id=1, pair=pair))

    assert result is not None
    assert "提取阶段模型调用失败" in result.comment
    assert result.consistency_flag == "needs_review"
    assert "不构成未响应推定" in result.reconcile_note
    assert stub.llm_prompt_built is False, "提取失败项不得触发 LLM 审查"
