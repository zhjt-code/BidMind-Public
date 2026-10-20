"""外层批次重试继承问题状态。

问题形态: 结构化 LLM 输出在内层校验重试耗尽后，批次外层整项重跑时丢掉
缺字段提示、半成品输出与已抬过的输出预算。

夹具: 缺 risk_level（本案例）与缺 score（同形态第二例）。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Optional
from unittest.mock import MagicMock, patch

from app.core.ai.llm_retry_resume import (
    DEFAULT_RAW_CLIP,
    LlmRetryResumeState,
    merge_resume_max_tokens,
)
from app.core.ai.reviewer_parts.review_output_budget import REVIEW_TOKEN_CEILING


def _valid_payload(**overrides: Any) -> str:
    data = {
        "requirement_id": 1,
        "bid_response_id": 2,
        "category": "technical",
        "requirement_summary": "技术方案要求",
        "response_summary": "已提供技术方案",
        "is_compliant": True,
        "compliance_level": "fully",
        "score": 90,
        "comment": "投标方案完整覆盖招标技术要求，无缺项。",
        "risk_level": "none",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def _payload_missing(*fields: str) -> str:
    data = json.loads(_valid_payload())
    for field in fields:
        data.pop(field, None)
    return json.dumps(data, ensure_ascii=False)


class _RecordingClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.max_tokens_seen: list[int] = []
        self.user_messages: list[str] = []

    async def chat(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1500,
        **_kwargs: Any,
    ) -> str:
        self.max_tokens_seen.append(max_tokens)
        self.user_messages.append(user_message)
        idx = min(len(self.max_tokens_seen) - 1, len(self._responses) - 1)
        return self._responses[idx]


def _build_orchestrator(client: _RecordingClient):
    from app.core.ai.reviewer import ReviewOrchestrator

    with patch("app.core.ai.reviewer.get_review_client") as mock_client:
        mock_client.return_value = MagicMock()
        orch = ReviewOrchestrator(db=MagicMock(), project_id=42)
    orch.llm_client = client
    orch._resolve_cd_schema = lambda: None
    return orch


def _run_call_llm_review(orch, *, resume_state=None, initial_max_tokens: int = 1500):
    resolved = MagicMock()
    resolved.temperature = 0.0
    resolved.max_tokens = initial_max_tokens
    registry = MagicMock()
    registry.get_prompt.return_value = resolved
    with patch("app.core.ai.reviewer.get_registry", return_value=registry), patch(
        "app.core.ai.reviewer._get_review_prompts",
        return_value=("sys", "user", "no_resp"),
    ):
        return asyncio.run(
            orch._call_llm_review(
                "prompt",
                operation_label="要求 1",
                resume_state=resume_state,
            )
        )


class TestResumeStateHelpers:
    def test_prompt_suffix_includes_hint_and_raw(self):
        state = LlmRetryResumeState(
            last_retry_hint="- risk_level: Field required",
            last_raw_output='{"score": 10}',
            last_max_tokens=6000,
        )
        suffix = state.prompt_suffix()
        assert "risk_level" in suffix
        assert '{"score": 10}' in suffix
        assert "补齐缺失字段" in suffix

    def test_empty_state_has_no_suffix(self):
        assert LlmRetryResumeState().prompt_suffix() == ""
        assert LlmRetryResumeState().has_payload() is False

    def test_raw_output_is_clipped(self):
        state = LlmRetryResumeState(last_raw_output="X" * (DEFAULT_RAW_CLIP + 80))
        suffix = state.prompt_suffix(raw_clip=DEFAULT_RAW_CLIP)
        assert "X" * 20 in suffix
        assert "已截断" in suffix
        assert len(suffix) < DEFAULT_RAW_CLIP + 200

    def test_merge_budget_uses_carried_amount_under_ceiling(self):
        state = LlmRetryResumeState(last_max_tokens=6000)
        assert merge_resume_max_tokens(1500, state, ceiling=REVIEW_TOKEN_CEILING) == 6000
        assert merge_resume_max_tokens(1500, None, ceiling=REVIEW_TOKEN_CEILING) == 1500
        over = LlmRetryResumeState(last_max_tokens=99999)
        assert merge_resume_max_tokens(1500, over, ceiling=REVIEW_TOKEN_CEILING) == REVIEW_TOKEN_CEILING


class TestCallLlmReviewInheritsResume:
    def test_first_attempt_uses_carried_budget_hint_and_raw(self):
        client = _RecordingClient([_valid_payload()])
        orch = _build_orchestrator(client)
        resume = LlmRetryResumeState(
            last_retry_hint="- score: Field required",
            last_raw_output='{"compliance_level":"fully"}',
            last_max_tokens=6000,
        )

        result = _run_call_llm_review(orch, resume_state=resume)

        assert result is not None
        assert client.max_tokens_seen[0] == 6000
        first_prompt = client.user_messages[0]
        assert "score" in first_prompt
        assert "Field required" in first_prompt
        assert '{"compliance_level":"fully"}' in first_prompt


class TestCallLlmReviewWritesResume:
    """内层耗尽后必须把问题状态写回同一对象，供外层闭包带走。"""

    def test_missing_risk_level_is_captured(self):
        client = _RecordingClient([_payload_missing("risk_level")])
        orch = _build_orchestrator(client)
        orch.max_retries = 1
        resume = LlmRetryResumeState()

        result = _run_call_llm_review(orch, resume_state=resume)

        assert result is None
        assert "risk_level" in resume.last_retry_hint
        assert "risk_level" not in json.loads(resume.last_raw_output)
        assert resume.last_max_tokens == 3000

    def test_missing_score_is_captured(self):
        """同形态第二例：换缺失字段仍写入同一状态对象。"""
        client = _RecordingClient([_payload_missing("score")])
        orch = _build_orchestrator(client)
        orch.max_retries = 1
        resume = LlmRetryResumeState()

        result = _run_call_llm_review(orch, resume_state=resume)

        assert result is None
        assert "score" in resume.last_retry_hint
        assert "score" not in json.loads(resume.last_raw_output)
        assert resume.last_max_tokens == 3000


class TestOuterRetryWiring:
    def test_review_single_pair_forwarder_passes_resume_state(self):
        from app.core.ai.reviewer import ReviewOrchestrator
        from app.core.ai.reviewer_parts import pair_review

        captured: dict[str, Any] = {}

        async def _fake(_self, _project_id, _pair, evidence_bundle=None, resume_state=None):
            captured["resume"] = resume_state
            return None

        with patch("app.core.ai.reviewer.get_review_client", return_value=MagicMock()):
            orch = ReviewOrchestrator(db=MagicMock(), project_id=1)
        resume = LlmRetryResumeState(last_max_tokens=6000)
        with patch.object(pair_review, "review_single_pair", _fake):
            asyncio.run(orch._review_single_pair(1, MagicMock(), resume_state=resume))

        assert captured["resume"] is resume

    def test_batch_retry_record_closes_over_resume_state(self):
        from app.core.ai.reviewer import ReviewOrchestrator

        src = inspect.getsource(ReviewOrchestrator._review_project_impl)
        assert "LlmRetryResumeState" in src
        assert "resume_state=_rs" in src
        assert "resume_state=resume" in src
