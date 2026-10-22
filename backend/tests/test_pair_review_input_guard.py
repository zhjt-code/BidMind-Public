"""投标审查 pair_review 链路 input-too-long 400 兜底测试。

覆盖:
- 收缩语义: 知识库优先牺牲、证据保底、首尾保留式截断(语义安全)
- input_guard 集成: 首次原样发送 → 400 后语义收缩重试成功
- fail-loud: 收缩到下限仍 400 时放弃该项(非静默原样重发)
- 零开销: 首次即成功时不触发收缩重建
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.reviewer import ReviewOrchestrator
from app.core.ai.reviewer_parts.pair_review import (
    _review_evidence_budgets,
    _scoring_supplement,
    _shrink_review_evidence,
)

pytestmark = pytest.mark.scoring


def _make_api_status_error(message: str, status_code: int = 400) -> APIStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status_code=status_code, request=request)
    return APIStatusError(message, response=response, body={"error": {"message": message}})


_VALID_REVIEW = {
    "requirement_id": 1,
    "category": "qualification",
    "requirement_summary": "资质",
    "response_summary": "已提供",
    "is_compliant": True,
    "compliance_level": "fully",
    "score": 95,
    "comment": "符合要求，附证据页 5",
    "risk_level": "none",
}


class _ScriptedJsonClient:
    """chat_json 脚本化: Exception 抛出, dict 返回; 记录每次 user_message。"""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.user_messages = []

    async def chat_json(self, **kwargs):
        self.user_messages.append(kwargs.get("user_message", ""))
        current = self.scripted.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class _FakeResolved:
    temperature = 0.0
    max_tokens = 1500
    system_message = "FAKE_SYS"
    user_template = "FAKE_USER"
    system_version_id = None
    user_version_id = None
    stability_class = "deterministic"


def _make_orchestrator(client) -> ReviewOrchestrator:
    orch = object.__new__(ReviewOrchestrator)
    orch.max_retries = 2
    orch.llm_client = client
    orch._project_id = None
    orch._resolve_cd_schema = lambda: {"type": "json_object"}
    return orch


# --------------------------- _scoring_supplement P0 A2 -------------------


def test_scoring_supplement_includes_authoritative_description_and_score_rules():
    """meta.authoritative_description 存在时, _scoring_supplement 应输出"评分表原文"字样
    及权威原文内容, 同时保留 score_rules 内容(不破坏既有功能)。"""
    req = SimpleNamespace(
        requirement_kind="scoring_item",
        is_key_review=1,
        score_weight=19,
        scoring_metadata={
            "max_score": 19,
            "volume": "technical",
            "score_rules": ["累计金额400万以上得满分19分", "线性插值公式"],
            "authoritative_description": (
                "根据自2024年5月11日至2026年5月11日中国境内招投标类软件产品销售业绩进行评分，"
                "累计金额达到400万元人民币及以上的得满分19分"
            ),
        },
    )
    pair = SimpleNamespace(requirement=req)
    result = _scoring_supplement(pair)
    assert "评分表原文" in result, "应输出评分表原文标签"
    assert "400万元人民币及以上的得满分19分" in result, "应包含权威原文内容"
    assert "累计金额400万以上得满分19分" in result, "score_rules 仍应输出"


def test_scoring_supplement_without_authoritative_description_unchanged():
    """无 authoritative_description 时, _scoring_supplement 行为与修改前完全一致（回归护栏）。"""
    req = SimpleNamespace(
        requirement_kind="scoring_item",
        is_key_review=1,
        score_weight=10,
        scoring_metadata={
            "max_score": 10,
            "volume": "technical",
            "score_rules": ["得满分10分"],
        },
    )
    pair = SimpleNamespace(requirement=req)
    result = _scoring_supplement(pair)
    assert "评分表原文" not in result, "无权威原文时不应出现该标签"
    assert "得满分10分" in result, "score_rules 仍应输出"
    assert "评分项·重点评审" in result, "块头标题不变"


def test_scoring_supplement_skips_rewritten_authoritative_description():
    """LLM 兜底标记 rewritten 时, 不得把改写文本作为"评分表原文"再次注入。"""
    req = SimpleNamespace(
        requirement_kind="scoring_item",
        is_key_review=1,
        score_weight=10,
        scoring_metadata={
            "max_score": 10,
            "volume": "technical",
            "score_rules": ["得满分10分"],
            "authoritative_description": "LLM改写后的评分标准摘要",
            "fidelity_flag": "rewritten",
        },
    )
    pair = SimpleNamespace(requirement=req)
    result = _scoring_supplement(pair)
    assert "评分表原文" not in result
    assert "LLM改写后的评分标准摘要" not in result
    assert "得满分10分" in result


# --------------------------- 收缩语义单元测 ---------------------------


def test_review_evidence_budgets_monotonic_and_evidence_floor():
    k1, e1 = _review_evidence_budgets(1)
    k2, e2 = _review_evidence_budgets(2)
    k3, e3 = _review_evidence_budgets(3)
    assert k1 >= k2 >= k3
    assert e1 >= e2 >= e3
    assert e3 >= 800  # 证据保底, 不会压到 0


def test_shrink_drops_knowledge_before_evidence():
    merged = "M" * 8000
    raw = "R" * 8000
    knowledge = "K" * 8000
    m2, r2, k2 = _shrink_review_evidence(merged, raw, knowledge, reduction_round=2)
    assert k2 == ""               # 知识库优先牺牲, round2 已丢弃
    assert m2 and r2              # 证据仍保留
    assert len(m2) < len(merged)  # 证据被压缩
    assert len(r2) < len(raw)


def test_shrink_keeps_ends_for_truncated_evidence():
    raw = "STARTaaa" + "z" * 20000 + "zzzEND"
    _, r1, _ = _shrink_review_evidence("", raw, "", reduction_round=1)
    assert r1.startswith("START")
    assert r1.endswith("END")
    assert "[TRUNCATED]" in r1


def test_shrink_round1_compresses_knowledge_but_may_keep():
    _, _, k1 = _shrink_review_evidence("", "", "K" * 8000, reduction_round=1)
    assert 0 < len(k1) < 8000


# --------------------------- input_guard 集成测 ---------------------------


def test_call_llm_review_retries_and_recovers_after_input_too_long():
    """首次输入超长 400 → 语义收缩重建后重试成功; prompt_rebuilder 被调用。"""
    import asyncio

    client = _ScriptedJsonClient([
        _make_api_status_error("Range of input length should be [1, 30720]"),
        dict(_VALID_REVIEW),
    ])
    orch = _make_orchestrator(client)

    rebuild_rounds = []

    def _rebuilder(reduction_round: int) -> str:
        rebuild_rounds.append(reduction_round)
        return f"REBUILT_ROUND_{reduction_round}"

    with patch("app.core.ai.reviewer.get_registry") as mock_reg:
        mock_reg.return_value.get_prompt.return_value = _FakeResolved()
        result = asyncio.run(orch._call_llm_review(
            "ORIGINAL_PROMPT",
            prompt_rebuilder=_rebuilder,
            operation_label="t-recover",
        ))

    assert result is not None
    assert rebuild_rounds == [1]                         # 收缩重建一次, round=1
    assert client.user_messages[0] == "ORIGINAL_PROMPT"  # 首次原样发送
    assert client.user_messages[1] == "REBUILT_ROUND_1"  # 重试用收缩后 prompt


def test_call_llm_review_fail_loud_when_shrink_exhausted(monkeypatch):
    """收缩到下限仍 400 → fail-loud return None(非静默, 不原样重发)。"""
    import asyncio

    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "REVIEW_INPUT_SHRINK_RETRIES", 1)

    client = _ScriptedJsonClient([
        _make_api_status_error("Range of input length should be [1, 30720]"),
        _make_api_status_error("Range of input length should be [1, 30720]"),
    ])
    orch = _make_orchestrator(client)

    def _rebuilder(reduction_round: int) -> str:
        return f"REBUILT_{reduction_round}"

    with patch("app.core.ai.reviewer.get_registry") as mock_reg:
        mock_reg.return_value.get_prompt.return_value = _FakeResolved()
        result = asyncio.run(orch._call_llm_review(
            "ORIGINAL", prompt_rebuilder=_rebuilder, operation_label="t-fail",
        ))

    assert result is None                  # fail-loud 放弃, 不静默成功
    assert len(client.user_messages) == 2  # round0 原样 + round1 收缩后放弃
    assert client.user_messages[0] == "ORIGINAL"
    assert client.user_messages[1] == "REBUILT_1"


def test_call_llm_review_first_attempt_no_rebuild_on_success():
    """首次即成功时不调用 prompt_rebuilder(零额外开销)。"""
    import asyncio

    client = _ScriptedJsonClient([dict(_VALID_REVIEW)])
    orch = _make_orchestrator(client)

    calls = []

    def _rebuilder(reduction_round: int) -> str:
        calls.append(reduction_round)
        return "SHOULD_NOT_BE_USED"

    with patch("app.core.ai.reviewer.get_registry") as mock_reg:
        mock_reg.return_value.get_prompt.return_value = _FakeResolved()
        result = asyncio.run(orch._call_llm_review(
            "ORIGINAL", prompt_rebuilder=_rebuilder, operation_label="t-ok",
        ))

    assert result is not None
    assert calls == []                          # rebuilder 未被调用
    assert client.user_messages == ["ORIGINAL"]
