"""CoVe / Judge 判定质量修复回归测试（critic-quality-fix）。

锁定的契约：

CoVe (`app.core.ai.critic.cove.run_cove`)
    - 无证据（chunks 空 + evidence_snippets 空）-> outcome="skipped",
      reason="no_evidence"，不再伪装成 inconclusive。
    - 未知 trigger -> outcome="skipped", reason="unknown_trigger"。
    - 有证据时，证据原文被渲染进问题 prompt（以便模型可引用）。
    - yes/no 均为 invalid（quote 回匹失败）-> inconclusive,
      reason="no_valid_quote"。
    - yes/no 平票 -> inconclusive, reason="tie_vote"。
    - abstain 允许 quote 空，不被强制改为 invalid。

Judge 抽样 (`app.core.ai.critic.judge.should_sample`)
    - 采样由 project_id 升级为 sample_key：同一 project 下不同 sample_key
      的采样决策可以不同；
    - 未传 sample_key 时回退到 project_id，保持向后兼容；
    - 高风险路径无视采样比例，强制采样。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.ai.critic import judge as judge_module
from app.core.ai.critic.cove import CoveResult, run_cove
from app.core.ai.critic.judge import should_sample


class _RecordingClient:
    """最小化假客户端，可配置每题的不同响应或抛错。"""

    def __init__(
        self,
        response: dict[str, Any] | list[dict[str, Any]] | None = None,
        raise_on_call: bool = False,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = response
        self._raise_on_call = raise_on_call

    async def chat_json(self, **kwargs: Any) -> Any:
        idx = len(self.calls)
        self.calls.append(kwargs)
        if self._raise_on_call:
            raise RuntimeError("simulated provider error")
        if isinstance(self._responses, list):
            return self._responses[idx % len(self._responses)]
        return self._responses or {
            "answer": "yes", "evidence_page": 1, "quote": "demo",
        }


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# CoVe：无证据 -> skipped + no_evidence
# ---------------------------------------------------------------------------


def test_run_cove_skips_when_no_evidence() -> None:
    client = _RecordingClient(
        response={"answer": "yes", "evidence_page": 1, "quote": "x"},
    )
    result = _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "demo", "mandatory_value": "demo",
            "chunk_page": "N/A",
        },
        chunks=[],
        llm_client=client,
        flow="investigate",
    ))
    assert isinstance(result, CoveResult)
    assert result.outcome == "skipped"
    assert result.reason == "no_evidence"
    assert result.should_escalate is False
    # 无证据时不应调用模型
    assert client.calls == []


def test_run_cove_skips_when_trigger_unknown() -> None:
    client = _RecordingClient()
    result = _run(run_cove(
        trigger="unknown_trigger_xyz",
        template_vars={},
        chunks=["demo quote"],
        llm_client=client,
        flow="investigate",
    ))
    assert result.outcome == "skipped"
    assert result.reason == "unknown_trigger"
    assert client.calls == []


# ---------------------------------------------------------------------------
# CoVe：证据会被渲染进 prompt
# ---------------------------------------------------------------------------


def test_run_cove_injects_evidence_into_prompt() -> None:
    evidence_text = "投标文件第5页关于业绩的具体条款原文内容A1B2C3"
    client = _RecordingClient(response={
        "answer": "yes", "evidence_page": 5, "quote": evidence_text[:30],
    })
    _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "业绩", "mandatory_value": "demo",
            "chunk_page": "5",
        },
        chunks=[evidence_text],
        llm_client=client,
        flow="investigate",
    ))
    assert client.calls, "expected at least one cove question call"
    for call in client.calls:
        user_msg = call.get("user_message") or ""
        assert "参考证据" in user_msg
        assert evidence_text[:20] in user_msg, (
            f"evidence text must be embedded in prompt, got: {user_msg[:200]}"
        )


def test_run_cove_uses_evidence_snippets_with_page_label() -> None:
    client = _RecordingClient(response={
        "answer": "yes", "evidence_page": 7, "quote": "锚点原文片段",
    })
    _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "业绩", "mandatory_value": "demo",
            "chunk_page": "7",
        },
        chunks=[],
        llm_client=client,
        flow="investigate",
        evidence_snippets=[{"page": 7, "text": "锚点原文片段"}],
    ))
    assert client.calls
    for call in client.calls:
        user_msg = call.get("user_message") or ""
        assert "第7页" in user_msg
        assert "锚点原文片段" in user_msg


# ---------------------------------------------------------------------------
# CoVe：_majority_vote reason 拆分
# ---------------------------------------------------------------------------


def test_run_cove_reports_no_valid_quote_when_all_quotes_unmatched() -> None:
    # 模型返回 yes 但 quote 与 chunks 毫无重叠，应全部被改写为 invalid
    client = _RecordingClient(response={
        "answer": "yes", "evidence_page": 1, "quote": "totally unrelated quote",
    })
    result = _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "demo", "mandatory_value": "demo",
            "chunk_page": "1",
        },
        chunks=["ABCDEFG 招标要求的具体内容"],
        llm_client=client,
        flow="investigate",
    ))
    assert result.outcome == "inconclusive"
    assert result.reason == "no_valid_quote"
    assert result.should_escalate is False
    assert all(a.answer == "invalid" for a in result.answers)


def test_run_cove_reports_tie_vote_when_yes_no_balanced() -> None:
    # 3 题分别返回 yes / no / abstain：yes=1, no=1, abstain 不计入有效票
    # quote 必须能回匹到 chunks, 否则被改为 invalid
    quote_text = "shared quote evidence"
    client = _RecordingClient(response=[
        {"answer": "yes", "evidence_page": 1, "quote": quote_text},
        {"answer": "no", "evidence_page": 1, "quote": quote_text},
        {"answer": "abstain", "evidence_page": 1, "quote": ""},
    ])
    result = _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "demo", "mandatory_value": "demo",
            "chunk_page": "1",
        },
        chunks=[quote_text],
        llm_client=client,
        flow="investigate",
    ))
    assert result.outcome == "inconclusive"
    assert result.reason == "tie_vote"
    assert result.should_escalate is False


def test_run_cove_reports_all_abstain_when_no_attempts_made() -> None:
    # 全部 abstain, quote 为空 - abstain 允许空 quote，不被改为 invalid
    client = _RecordingClient(response={
        "answer": "abstain", "evidence_page": 0, "quote": "",
    })
    result = _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "demo", "mandatory_value": "demo",
            "chunk_page": "1",
        },
        chunks=["some evidence"],
        llm_client=client,
        flow="investigate",
    ))
    assert result.outcome == "inconclusive"
    assert result.reason == "all_abstain_or_error"
    # abstain 不应被强制升级为 invalid
    assert all(a.answer == "abstain" for a in result.answers)


def test_run_cove_disagree_still_escalates() -> None:
    quote_text = "shared quote evidence"
    client = _RecordingClient(response={
        "answer": "no", "evidence_page": 1, "quote": quote_text,
    })
    result = _run(run_cove(
        trigger="investigate_not_mandatory",
        template_vars={
            "clause_keyword": "demo", "mandatory_value": "demo",
            "chunk_page": "1",
        },
        chunks=[quote_text],
        llm_client=client,
        flow="investigate",
    ))
    assert result.outcome == "disagree"
    assert result.reason == ""
    assert result.should_escalate is True


# ---------------------------------------------------------------------------
# Judge：条目级采样 sample_key
# ---------------------------------------------------------------------------


@pytest.fixture
def _judge_enabled(monkeypatch: pytest.MonkeyPatch):
    """确保 JUDGE_ENABLED=True，比率可控。"""
    monkeypatch.setattr(judge_module.settings, "JUDGE_ENABLED", True)
    yield monkeypatch


def test_should_sample_high_risk_always_true(_judge_enabled) -> None:
    _judge_enabled.setattr(judge_module.settings, "JUDGE_SAMPLING_RATIO", 0.0)
    # 即使 ratio=0, 高风险依然 100%
    assert should_sample(project_id=1, is_high_risk=True) is True
    assert should_sample(
        project_id=1, is_high_risk=True, sample_key="1:req-999",
    ) is True


def test_should_sample_zero_ratio_skips_non_high_risk(_judge_enabled) -> None:
    _judge_enabled.setattr(judge_module.settings, "JUDGE_SAMPLING_RATIO", 0.0)
    assert should_sample(project_id=1, is_high_risk=False) is False
    assert should_sample(
        project_id=1, is_high_risk=False, sample_key="1:req-1",
    ) is False


def test_should_sample_full_ratio_always_true(_judge_enabled) -> None:
    _judge_enabled.setattr(judge_module.settings, "JUDGE_SAMPLING_RATIO", 1.0)
    assert should_sample(project_id=1, sample_key="1:req-42") is True


def test_should_sample_sample_key_falls_back_to_project_id(
    _judge_enabled,
) -> None:
    """不传 sample_key 时应与旧行为一致（按 project_id hash）。"""
    _judge_enabled.setattr(judge_module.settings, "JUDGE_SAMPLING_RATIO", 0.1)
    # 同一 project_id 多次调用, 结果必须幂等
    results = {should_sample(project_id=12345) for _ in range(5)}
    assert len(results) == 1


def test_should_sample_item_level_differentiates_within_project(
    _judge_enabled,
) -> None:
    """同一 project 下不同 sample_key 必须能产生不同采样结果，
    否则等同于"全采/全不采"的项目级偏差。"""
    _judge_enabled.setattr(judge_module.settings, "JUDGE_SAMPLING_RATIO", 0.1)
    project_id = 777
    sampled = set()
    not_sampled = set()
    for i in range(200):
        key = f"{project_id}:req-{i}"
        if should_sample(project_id=project_id, sample_key=key):
            sampled.add(i)
        else:
            not_sampled.add(i)
    assert sampled, "item-level sampling never returned True"
    assert not_sampled, "item-level sampling never returned False"
    # 10% 采样在 200 条上期望 ~20 条，允许 [5, 60] 区间
    assert 5 <= len(sampled) <= 60, (
        f"sampling ratio drifted: {len(sampled)}/200"
    )


def test_should_sample_is_deterministic_per_sample_key(_judge_enabled) -> None:
    _judge_enabled.setattr(judge_module.settings, "JUDGE_SAMPLING_RATIO", 0.1)
    key = "777:req-9"
    first = should_sample(project_id=777, sample_key=key)
    for _ in range(5):
        assert should_sample(project_id=777, sample_key=key) is first


# ---------------------------------------------------------------------------
# Review 主判确定性：温度收口到 0.0
# ---------------------------------------------------------------------------


def test_reviewer_main_judgement_uses_zero_temperature() -> None:
    """锁定 review 主判路径的 temperature=0.0，与 Judge 对齐以减少伪 disagree。

    静态读取 reviewer._call_llm_review 源码，断言未再出现 temperature=0.1。
    """
    import inspect

    from app.core.ai.reviewer import ReviewOrchestrator

    src = inspect.getsource(ReviewOrchestrator._call_llm_review)
    assert "temperature=0.0" in src
    assert "temperature=0.1" not in src, (
        "reviewer main judgement must run at temperature=0.0 (critic-quality-fix)"
    )
