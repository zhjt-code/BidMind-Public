"""审查输出预算截断自愈与失败可见性防御测试。

真实风险: 评分项审查 prompt 额外要求 comment 200-400 字与 rule_assessments 数组,
输出量约为普通要求的 3~4 倍(实测普通要求 comment 中位 113 字)。共用节点预算 1500
时输出撞顶被截断, cloud_client 抢救出部分字段后必填字段缺失, Pydantic 校验必败;
重试仅追加提示语、预算不变, 构成确定性重放(同源), 实测评分项 132/138 失败
而普通要求 0/289。同时两处可观测性缺陷把它盖住: 校验日志只报错误个数不报字段;
retry_tracker 用"非 None 即恢复"把返回兜底对象的失败批次报成 recovered=N/N。

断言契约:
1. 必填字段缺失判为疑似截断; 内容不合规类错误不触发扩容(扩容对其无用)
2. 扩容翻倍且封顶, 非法入参不抛错
3. call_llm_review 在截断型校验失败后第二次调用必须用扩容后的预算
4. 校验失败 message 必须含字段路径与原因(不能只有个数)
5. 兜底结果不得计入 recovered; 有效结果正常计入
6. review 侧 tracker 注入的谓词必须与 is_failure_result 同源
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional
from unittest.mock import MagicMock, patch

from app.core.ai.retry_tracker import LLMRetryTracker
from app.core.ai.reviewer_parts.result_builder import (
    FAILURE_COMMENT_MARKER,
    is_failure_result,
)
from app.core.ai.reviewer_parts.review_output_budget import (
    REVIEW_TOKEN_CEILING,
    escalate_review_max_tokens,
    looks_like_truncated_output,
)
from app.core.ai.validators import get_review_result_validator

# ---------------------------------------------------------------------------
# 1-2. 截断判定与扩容
# ---------------------------------------------------------------------------


class TestTruncationDetection:
    """截断判定: 必填字段缺失=强信号; 其余类型扩容无用不得触发。"""

    def test_missing_required_field_is_truncation_signal(self):
        errors = [{"loc": ("comment",), "msg": "Field required", "type": "missing"}]
        assert looks_like_truncated_output(errors) is True

    def test_nested_missing_in_rule_assessments_is_truncation_signal(self):
        """截断常发生在数组尾部元素, 嵌套 missing 同样是信号。"""
        errors = [
            {
                "loc": ("rule_assessments", 0, "reason"),
                "msg": "Field required",
                "type": "missing",
            }
        ]
        assert looks_like_truncated_output(errors) is True

    def test_content_violation_types_do_not_trigger_expansion(self):
        """超长/枚举不合规是内容问题, 抬预算无用, 不得误触发扩容。"""
        errors = [
            {"loc": ("requirement_summary",), "msg": "too long", "type": "string_too_long"},
            {"loc": ("compliance_level",), "msg": "bad enum", "type": "literal_error"},
            {"loc": ("score",), "msg": "not int", "type": "int_parsing"},
        ]
        assert looks_like_truncated_output(errors) is False

    def test_empty_and_none_are_safe(self):
        assert looks_like_truncated_output(None) is False
        assert looks_like_truncated_output([]) is False

    def test_malformed_error_entries_do_not_raise(self):
        """判定失败只应退回"非截断", 不得因判定本身中断审查。"""
        assert looks_like_truncated_output(["not a dict", None, 42]) is False


class TestBudgetEscalation:
    """扩容: 翻倍 + 封顶 + 非法入参不抛错。"""

    def test_doubles_below_ceiling(self):
        assert escalate_review_max_tokens(1500) == 3000
        assert escalate_review_max_tokens(3000) == 6000

    def test_caps_at_ceiling(self):
        assert escalate_review_max_tokens(6000) == REVIEW_TOKEN_CEILING
        assert escalate_review_max_tokens(REVIEW_TOKEN_CEILING) == REVIEW_TOKEN_CEILING

    def test_invalid_input_returns_ceiling_without_raising(self):
        assert escalate_review_max_tokens(0) == REVIEW_TOKEN_CEILING
        assert escalate_review_max_tokens(-100) == REVIEW_TOKEN_CEILING
        assert escalate_review_max_tokens("bad") == REVIEW_TOKEN_CEILING  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. call_llm_review 端到端: 截断后第二次调用必须扩容
# ---------------------------------------------------------------------------


def _truncated_payload() -> str:
    """模拟被截断后经 JSON 修复抢救出的部分字段(必填 comment 等缺失)。"""
    return json.dumps(
        {
            "requirement_id": 1,
            "category": "technical",
            "requirement_summary": "技术方案要求",
        },
        ensure_ascii=False,
    )


def _valid_payload() -> str:
    return json.dumps(
        {
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
        },
        ensure_ascii=False,
    )


class _RecordingClient:
    """记录每次调用 max_tokens 的假 LLM 客户端。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.max_tokens_seen: list[int] = []

    async def chat(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1500,
        **_kwargs: Any,
    ) -> str:
        self.max_tokens_seen.append(max_tokens)
        idx = min(len(self.max_tokens_seen) - 1, len(self._responses) - 1)
        return self._responses[idx]


def _build_orchestrator(client: _RecordingClient):
    from app.core.ai.reviewer import ReviewOrchestrator

    with patch("app.core.ai.reviewer.get_review_client") as mock_client:
        mock_client.return_value = MagicMock()
        orch = ReviewOrchestrator(db=MagicMock(), project_id=42)
    orch.llm_client = client
    # cd_schema=None 走 chat 路径, 便于断言 max_tokens 透传
    orch._resolve_cd_schema = lambda: None
    return orch


def _run_call_llm_review(orch, *, initial_max_tokens: int = 1500):
    resolved = MagicMock()
    resolved.temperature = 0.0
    resolved.max_tokens = initial_max_tokens
    registry = MagicMock()
    registry.get_prompt.return_value = resolved
    with patch("app.core.ai.reviewer.get_registry", return_value=registry), patch(
        "app.core.ai.reviewer._get_review_prompts",
        return_value=("sys", "user", "no_resp"),
    ):
        return asyncio.run(orch._call_llm_review("prompt", operation_label="要求 1"))


class TestCallLlmReviewBudgetSelfHealing:
    """根因修复锁定: 截断型校验失败必须抬预算重试, 而非同预算重放。"""

    def test_truncation_triggers_expanded_retry_and_recovers(self):
        """首轮截断 → 第二轮预算翻倍并成功返回结果。"""
        client = _RecordingClient([_truncated_payload(), _valid_payload()])
        orch = _build_orchestrator(client)

        result = _run_call_llm_review(orch, initial_max_tokens=1500)

        assert result is not None, "扩容重试后应取得有效结果"
        assert client.max_tokens_seen[:2] == [1500, 3000], (
            f"第二次调用必须使用扩容预算, 实际: {client.max_tokens_seen}"
        )

    def test_repeated_truncation_keeps_escalating_under_ceiling(self):
        """连续截断时预算持续抬升且不越过上限。"""
        client = _RecordingClient([_truncated_payload()])
        orch = _build_orchestrator(client)
        orch.max_retries = 3

        result = _run_call_llm_review(orch, initial_max_tokens=1500)

        assert result is None
        assert client.max_tokens_seen == [1500, 3000, 6000]
        assert all(t <= REVIEW_TOKEN_CEILING for t in client.max_tokens_seen)

    def test_content_violation_does_not_expand_budget(self):
        """内容不合规(超长)时不得抬预算——扩容对其无效, 只会浪费 token。"""
        too_long = json.loads(_valid_payload())
        too_long["requirement_summary"] = "长" * 80  # schema 上限 50
        client = _RecordingClient([json.dumps(too_long, ensure_ascii=False)])
        orch = _build_orchestrator(client)
        orch.max_retries = 2

        _run_call_llm_review(orch, initial_max_tokens=1500)

        assert client.max_tokens_seen == [1500, 1500], (
            f"非截断错误不应扩容, 实际: {client.max_tokens_seen}"
        )


# ---------------------------------------------------------------------------
# 4. 校验失败可观测性
# ---------------------------------------------------------------------------


class TestValidationMessageObservability:
    """校验失败 message 必须暴露字段路径与原因, 而非只报个数。"""

    def test_message_contains_field_path_and_reason(self):
        validator = get_review_result_validator()
        result = validator.validate(_truncated_payload())

        assert result.success is False
        message = result.error.message
        assert "个错误" in message, "错误个数应保留"
        assert "Field required" in message or "required" in message.lower(), (
            f"message 必须含 Pydantic 原因: {message}"
        )
        assert any(
            field in message for field in ("comment", "response_summary", "is_compliant")
        ), f"message 必须含缺失字段路径: {message}"

    def test_message_does_not_leak_raw_input_values(self):
        """摘要只取 loc 与 msg, 不得带出 input 值(防文档内容进日志)。"""
        payload = json.loads(_valid_payload())
        secret = "机密投标报价一亿两千万元整"
        payload["requirement_summary"] = secret * 5  # 触发 string_too_long
        validator = get_review_result_validator()
        result = validator.validate(json.dumps(payload, ensure_ascii=False))

        assert result.success is False
        assert secret not in result.error.message


# ---------------------------------------------------------------------------
# 5-6. 恢复判定不得把兜底结果当成功
# ---------------------------------------------------------------------------


class _FakeReviewResult:
    def __init__(self, comment: str) -> None:
        self.comment = comment
        self.requirement_id = 1


class TestFailureResultRecognition:
    """兜底结果识别: 生产者与识别者同源。"""

    def test_failure_marker_recognized(self):
        assert is_failure_result(_FakeReviewResult(f"审查失败[{FAILURE_COMMENT_MARKER}]：xxx")) is True

    def test_valid_result_not_recognized_as_failure(self):
        assert is_failure_result(_FakeReviewResult("投标方案完整覆盖要求")) is False

    def test_none_is_failure(self):
        assert is_failure_result(None) is True


class TestRetryTrackerRecoveryPredicate:
    """retry_tracker: 注入谓词后兜底结果不得计入 recovered。"""

    def test_fallback_result_not_counted_as_recovered(self):
        """核心回归: 11 条全失败的批次不得报成 recovered=11。"""
        tracker = LLMRetryTracker(
            "reviewer", is_recovered=lambda r: not is_failure_result(r),
        )
        failure = _FakeReviewResult(f"审查失败[{FAILURE_COMMENT_MARKER}]：xxx")
        for idx in range(3):
            tracker.record(
                key=f"req_{idx}",
                retry_coro_factory=lambda _f=failure: _return(_f),
            )

        stats = asyncio.run(tracker.retry_all_concurrent(max_concurrency=2))

        assert stats.retried == 3
        assert stats.recovered == 0, "兜底结果被误计为恢复, 会把全量失败伪装成自愈"
        assert all(d["success"] is False for d in stats.details)

    def test_valid_result_counted_as_recovered(self):
        tracker = LLMRetryTracker(
            "reviewer", is_recovered=lambda r: not is_failure_result(r),
        )
        tracker.record(
            key="req_1",
            retry_coro_factory=lambda: _return(_FakeReviewResult("有效审查结论")),
        )

        stats = asyncio.run(tracker.retry_all_concurrent())

        assert stats.recovered == 1

    def test_default_predicate_preserves_legacy_behaviour(self):
        """未注入谓词的既有调用方(如素材打标)行为不变: 非 None 即恢复。"""
        tracker = LLMRetryTracker("material_tagger")
        tracker.record(key="m1", retry_coro_factory=lambda: _return({"tags": ["a"]}))
        tracker.record(key="m2", retry_coro_factory=lambda: _return(None))

        stats = asyncio.run(tracker.retry_all())

        assert stats.retried == 2
        assert stats.recovered == 1

    def test_predicate_exception_counts_as_not_recovered(self):
        """谓词自身异常必须按未恢复处理, 不得把失败静默算成成功。"""

        def _boom(_result: Any) -> bool:
            raise RuntimeError("predicate boom")

        tracker = LLMRetryTracker("reviewer", is_recovered=_boom)
        tracker.record(key="req_1", retry_coro_factory=lambda: _return("anything"))

        stats = asyncio.run(tracker.retry_all())

        assert stats.recovered == 0


async def _return(value: Any) -> Any:
    return value


# ---------------------------------------------------------------------------
# 7. reviewer 侧接线契约
# ---------------------------------------------------------------------------


def test_reviewer_tracker_rejects_failure_results():
    """ReviewOrchestrator 必须给 tracker 注入能识别兜底结果的谓词。"""
    from app.core.ai.reviewer import ReviewOrchestrator

    with patch("app.core.ai.reviewer.get_review_client") as mock_client:
        mock_client.return_value = MagicMock()
        orch = ReviewOrchestrator(db=MagicMock(), project_id=42)

    predicate = orch._retry_tracker._is_recovered
    assert predicate(_FakeReviewResult(f"审查失败[{FAILURE_COMMENT_MARKER}]：x")) is False
    assert predicate(_FakeReviewResult("有效结论")) is True


def test_review_item_registry_budget_is_raised():
    """seed 侧 review 两节点预算必须已上调(1500 装不下评分项输出)。"""
    import re
    from pathlib import Path

    seed_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "seed_llm_registry.py"
    )
    text = seed_path.read_text(encoding="utf-8")
    for node_key in ("bid_review.review_item", "bid_review.review_no_response"):
        idx = text.index(f'"{node_key}"')
        window = text[idx : idx + 400]
        budgets = [int(m) for m in re.findall(r"0\.1,\s*(\d+),", window)]
        assert budgets, f"{node_key} 未解析到 max_tokens"
        assert budgets[0] >= 3000, f"{node_key} 预算回退到 {budgets[0]}, 评分项将再次截断"
