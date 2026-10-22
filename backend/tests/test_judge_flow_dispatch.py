"""P1.2 防御测试：Judge 按 flow 分流到不同 prompt (走 Registry 优先 + 代码兜底)。"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.ai.critic.flows import (
    CRITIC_BUSINESS_FLOWS,
    FLOW_COLLUSION,
    FLOW_GENERIC,
    FLOW_INVESTIGATE,
    FLOW_REVIEW,
    FLOW_WRITER,
)
from app.core.ai.critic.judge import run_judge
from app.core.ai.critic.prompts.judge_templates import (
    JUDGE_COLLUSION_SYSTEM,
    JUDGE_GENERIC_SYSTEM,
    JUDGE_REVIEW_SYSTEM,
    JUDGE_WRITER_SYSTEM,
    get_judge_templates,
)


class TestGetJudgeTemplates:

    @pytest.mark.parametrize("flow,expected_suffix", [
        (FLOW_REVIEW, "review"),
        (FLOW_COLLUSION, "collusion"),
        (FLOW_WRITER, "writer"),
        # reviewer 主链路 flow="investigate" 必须复用 review 复核模板，
        # 而不是回退 generic。
        (FLOW_INVESTIGATE, "review"),
        ("INVESTIGATE", "review"),
        (FLOW_GENERIC, "generic"),
    ])
    def test_known_flows(self, flow: str, expected_suffix: str):
        suffix, system, user = get_judge_templates(flow)
        assert suffix == expected_suffix
        assert "{original_output}" in user
        assert "{context_summary}" in user
        assert "{evidence_text}" in user

    def test_investigate_flow_uses_review_system(self):
        """: 投标审查 flow 必须命中 review 复核模板而非 generic。"""
        suffix, system, _user = get_judge_templates(FLOW_INVESTIGATE)
        assert suffix == "review"
        assert system == JUDGE_REVIEW_SYSTEM
        assert system != JUDGE_GENERIC_SYSTEM

    @pytest.mark.parametrize("flow", ["unknown", "", None])
    def test_unknown_flow_returns_generic(self, flow):
        suffix, system, _user = get_judge_templates(flow or "")
        assert suffix == "generic"
        assert system == JUDGE_GENERIC_SYSTEM

    def test_all_business_flows_have_non_generic_template(self):
        """所有 Critic 业务 flow 必须显式命中模板，不得回退 generic。"""
        for flow in CRITIC_BUSINESS_FLOWS:
            suffix, system, _user = get_judge_templates(flow)
            assert suffix != "generic", f"{flow} should not fall back to generic"
            assert system != JUDGE_GENERIC_SYSTEM


class TestRunJudgePromptDispatch:
    """捕获 llm_client.chat_json 的入参, 断言按 flow 取到正确的 system prompt。"""

    @pytest.mark.asyncio
    async def test_writer_flow_uses_writer_system(self):
        captured: dict = {}

        async def fake_chat_json(**kwargs):
            captured.update(kwargs)
            return {"verdict": "agree", "confidence": 0.9, "reason": "ok"}

        client = AsyncMock()
        client.chat_json = fake_chat_json

        await run_judge(
            original_output={"node_id": 1, "content": "某章节正文"},
            context_summary="章节: [free_writing] 技术方案",
            llm_client=client,
            flow=FLOW_WRITER,
            evidence={
                "bound_materials": [{"title": "公司简介", "snippet": "XX 公司成立于..."}],
                "hard_constraints": ["不得转包"],
            },
        )

        # registry_first 会对 system 执行 .format() 解转义 {{}} → {}
        assert captured["system_message"] == JUDGE_WRITER_SYSTEM.format()
        assert "[绑定素材]" in captured["user_message"]
        assert "[硬约束]" in captured["user_message"]
        assert captured["max_tokens"] == 2000  # writer 专用预算(提升)

    @pytest.mark.asyncio
    async def test_review_flow_uses_review_system(self):
        captured: dict = {}

        async def fake_chat_json(**kwargs):
            captured.update(kwargs)
            return {"verdict": "agree", "confidence": 0.8, "reason": "ok"}

        client = AsyncMock()
        client.chat_json = fake_chat_json

        await run_judge(
            original_output={"review_id": 1},
            context_summary="某审查上下文",
            llm_client=client,
            flow=FLOW_REVIEW,
        )

        assert captured["system_message"] == JUDGE_REVIEW_SYSTEM.format()
        assert captured["max_tokens"] == 1500  # 提升后预算(review/collusion/generic)

    @pytest.mark.asyncio
    async def test_collusion_flow_uses_collusion_system(self):
        captured: dict = {}

        async def fake_chat_json(**kwargs):
            captured.update(kwargs)
            return {"verdict": "need_review", "confidence": 0.7, "reason": "uncertain"}

        client = AsyncMock()
        client.chat_json = fake_chat_json

        await run_judge(
            original_output={"pair": "A-B"},
            context_summary="维度: entity",
            llm_client=client,
            flow=FLOW_COLLUSION,
        )

        assert captured["system_message"] == JUDGE_COLLUSION_SYSTEM.format()
        assert captured["max_tokens"] == 1500  # 提升后预算(review/collusion/generic)

    @pytest.mark.asyncio
    async def test_investigate_flow_uses_review_system(self):
        """: reviewer 调用 run_judge(flow="investigate") 时，
 必须使用 review 复核模板，避免日志中 judge.generic 未注册回退。"""
        captured: dict = {}

        async def fake_chat_json(**kwargs):
            captured.update(kwargs)
            return {"verdict": "agree", "confidence": 0.8, "reason": "ok"}

        client = AsyncMock()
        client.chat_json = fake_chat_json

        await run_judge(
            original_output={"compliance_level": "not"},
            context_summary="招标要求(ID=1): demo",
            llm_client=client,
            flow=FLOW_INVESTIGATE,
        )

        assert captured["system_message"] == JUDGE_REVIEW_SYSTEM.format()
        assert captured["max_tokens"] == 1500  # 提升后预算(review/collusion/generic)

    @pytest.mark.asyncio
    async def test_unknown_flow_falls_back_to_generic(self):
        captured: dict = {}

        async def fake_chat_json(**kwargs):
            captured.update(kwargs)
            return {"verdict": "agree", "confidence": 0.5, "reason": "x"}

        client = AsyncMock()
        client.chat_json = fake_chat_json

        await run_judge(
            original_output={"x": 1},
            context_summary="...",
            llm_client=client,
            flow="unknown_flow_type",
        )

        assert captured["system_message"] == JUDGE_GENERIC_SYSTEM.format()
        assert captured["max_tokens"] == 1500  # 提升后预算(review/collusion/generic)

    @pytest.mark.asyncio
    async def test_evidence_none_still_renders_placeholder(self):
        """evidence=None 时, evidence_text 应有 "(无附加证据)" 占位。"""
        captured: dict = {}

        async def fake_chat_json(**kwargs):
            captured.update(kwargs)
            return {"verdict": "agree", "confidence": 0.5, "reason": "ok"}

        client = AsyncMock()
        client.chat_json = fake_chat_json

        await run_judge(
            original_output={"a": 1},
            context_summary="...",
            llm_client=client,
            flow=FLOW_WRITER,
            evidence=None,
        )

        assert "(无附加证据)" in captured["user_message"]

    @pytest.mark.asyncio
    async def test_no_client_returns_skipped(self):
        """llm_client=None 时短路返回 skipped, 不抛异常。"""
        result = await run_judge(
            original_output={},
            context_summary="",
            llm_client=None,
            flow=FLOW_WRITER,
        )
        assert result.verdict == "skipped"
