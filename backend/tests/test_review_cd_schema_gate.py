"""评分项审查禁用 json_object 模式的防御测试。

真实风险: CONSTRAINED_DECODING_RATIO_REVIEW=1.0 全量启用 {"type":"json_object"},
该模式下 provider 对嵌套对象数组生成结构性退化——对照实验(req37010, 同 prompt/
模型/温度/8192 预算): json_object 产出 `[{}]` 元素+标量 evidence_pages+幻觉键,
纯 chat 三元素完全正确。顶层平铺字段不受影响, 故普通要求 0/289 失败而唯一要求
嵌套数组(rule_assessments)的评分项全灭。

断言契约:
1. expects_rule_assessments 与 scoring_supplement 的 rule_assessments 注入条件同源
2. 评分项(expects_nested_array_output=True)强制走纯 chat, 不得调用 chat_json
3. 普通要求在灰度开启时仍走 chat_json(保留减少 jsonrepair 的收益)
4. review_single_pair 接线锚点: 判定必须来自 expects_rule_assessments 单源
"""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.core.ai.reviewer_parts.scoring_rubric import (
    expects_rule_assessments,
    scoring_supplement,
)

pytestmark = pytest.mark.scoring


class _FakeRequirement:
    def __init__(
        self,
        *,
        kind: str = "scoring_item",
        is_key: int = 1,
        meta: Optional[dict] = None,
    ) -> None:
        self.requirement_kind = kind
        self.is_key_review = is_key
        self.scoring_metadata = meta
        self.score_weight = 15.0


class _FakePair:
    def __init__(self, requirement: Optional[_FakeRequirement]) -> None:
        self.requirement = requirement


# ---------------------------------------------------------------------------
# 1. 判定与 prompt 注入条件同源
# ---------------------------------------------------------------------------


class TestExpectsRuleAssessments:
    """判定条件必须与 scoring_supplement 的注入条件同源, 禁止口径分裂。"""

    def test_scoring_item_with_rules_expects_nested_and_prompt_injects(self):
        pair = _FakePair(_FakeRequirement(meta={"score_rules": ["优:14-15分"]}))
        assert expects_rule_assessments(pair) is True
        assert "rule_assessments" in scoring_supplement(pair)

    def test_scoring_item_without_rules_no_nested_and_prompt_omits(self):
        """无评分/扣分规则时 prompt 不注入 rule_assessments, 判定必须同步为 False。"""
        pair = _FakePair(_FakeRequirement(meta={"score_rules": []}))
        assert expects_rule_assessments(pair) is False
        assert "rule_assessments" not in scoring_supplement(pair)

    def test_deduction_only_rules_also_expect_nested(self):
        pair = _FakePair(_FakeRequirement(meta={"deduction_rules": ["每项扣1分"]}))
        assert expects_rule_assessments(pair) is True

    def test_normal_requirement_never_expects_nested(self):
        pair = _FakePair(_FakeRequirement(kind="normal", is_key=0, meta={"score_rules": ["x"]}))
        assert expects_rule_assessments(pair) is False

    def test_missing_requirement_is_safe(self):
        assert expects_rule_assessments(_FakePair(None)) is False


# ---------------------------------------------------------------------------
# 2-3. 路由: 嵌套数组形态走纯 chat, 普通形态保持 json_object
# ---------------------------------------------------------------------------


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


class _RouteRecordingClient:
    """记录走了哪条调用路径的假 LLM 客户端。"""

    def __init__(self) -> None:
        self.chat_calls = 0
        self.chat_json_calls = 0

    async def chat(self, **_kwargs: Any) -> str:
        self.chat_calls += 1
        return _valid_payload()

    async def chat_json(self, **_kwargs: Any) -> dict:
        self.chat_json_calls += 1
        return json.loads(_valid_payload())


def _run_call(client: _RouteRecordingClient, *, expects_nested: bool):
    from app.core.ai.reviewer import ReviewOrchestrator

    with patch("app.core.ai.reviewer.get_review_client") as mock_client:
        mock_client.return_value = MagicMock()
        orch = ReviewOrchestrator(db=MagicMock(), project_id=42)
    orch.llm_client = client
    # 模拟灰度全量开启（生产现状 CONSTRAINED_DECODING_RATIO_REVIEW=1.0）
    orch._resolve_cd_schema = lambda: {"type": "json_object"}

    resolved = MagicMock()
    resolved.temperature = 0.0
    resolved.max_tokens = 3000
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
                expects_nested_array_output=expects_nested,
            )
        )


def test_nested_array_output_forces_plain_chat():
    """ 核心: 要求嵌套数组时即使灰度开启也必须走纯 chat。"""
    client = _RouteRecordingClient()

    result = _run_call(client, expects_nested=True)

    assert result is not None
    assert client.chat_calls == 1
    assert client.chat_json_calls == 0, "评分项走 json_object 会复现 [{}] 结构退化"


def test_flat_output_keeps_json_object_route():
    """回归: 普通要求在灰度开启时仍走 chat_json, 不得连带关闭。"""
    client = _RouteRecordingClient()

    result = _run_call(client, expects_nested=False)

    assert result is not None
    assert client.chat_json_calls == 1
    assert client.chat_calls == 0


# ---------------------------------------------------------------------------
# 4. 接线锚点
# ---------------------------------------------------------------------------


def test_review_single_pair_wires_expects_rule_assessments():
    """review_single_pair 必须以 expects_rule_assessments 单源判定传参。"""
    from app.core.ai.reviewer_parts import pair_review

    source = inspect.getsource(pair_review.review_single_pair)
    assert "expects_nested_array_output=expects_rule_assessments(pair)" in source, (
        "接线丢失或判定改为另写判据——必须与 prompt 注入条件同源"
    )
