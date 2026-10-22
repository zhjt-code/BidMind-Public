""" 防御测试: 多候选 JSON 回退。

锁定契约:
- extract_json_payload_candidates 使用标准 JSON 词法分析器按序提取全部顶层候选，
 字符串值内的 []/{} 不得被误判（教训）。
- recover_validated_payload 在首个 payload 校验失败时逐个尝试其余候选，
 跳过与失败 payload 相等的候选，全部失败返回 None。
- 真实场景回归: 模型回显 Prompt 内评分基准常量 {"expected_total": 100.0} 后
 输出完整 FullCandidateScope，回退必须能拿到真实答案。
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.ai.json_payload_recovery import (
    extract_json_payload_candidates,
    recover_validated_payload,
)
from app.core.extractor.scoring.scoring_full_candidate_contracts import (
    FullCandidateScope,
)

pytestmark = pytest.mark.scoring


class _MiniScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: str
    lot: str
    expected_total: float


# ---------------------------------------------------------------------------
# extract_json_payload_candidates
# ---------------------------------------------------------------------------


def test_extract_returns_all_top_level_candidates_in_order() -> None:
    text = (
        '评分基准: {"expected_total": 100.0}\n'
        '正式输出如下:\n'
        '{"scheme": "s1", "lot": "__single__", "expected_total": 100.0}'
    )
    candidates = extract_json_payload_candidates(text)
    assert len(candidates) == 2
    assert json.loads(candidates[0]) == {"expected_total": 100.0}
    assert json.loads(candidates[1])["scheme"] == "s1"


def test_extract_ignores_brackets_inside_string_values() -> None:
    # 教训: 字符串值内的 []/{} 不得被裸计数误判
    text = '{"rule": "扣分标准见[附录]{表2}", "score": 5}'
    candidates = extract_json_payload_candidates(text)
    assert len(candidates) == 1
    assert json.loads(candidates[0])["score"] == 5


def test_extract_handles_no_json_and_empty_input() -> None:
    assert extract_json_payload_candidates("纯文本没有任何JSON") == []
    assert extract_json_payload_candidates("") == []


def test_extract_respects_max_candidates_cap() -> None:
    text = " ".join('{"i": %d}' % i for i in range(20))
    candidates = extract_json_payload_candidates(text, max_candidates=8)
    assert len(candidates) == 8


# ---------------------------------------------------------------------------
# recover_validated_payload
# ---------------------------------------------------------------------------


def test_recover_skips_failed_first_payload_and_validates_second() -> None:
    text = (
        '评分基准: {"expected_total": 100.0}\n'
        '{"scheme": "s1", "lot": "__single__", "expected_total": 100.0}'
    )
    recovered = recover_validated_payload(
        text, _MiniScope, skip_payload={"expected_total": 100.0},
    )
    assert recovered is not None
    assert recovered.scheme == "s1"
    assert recovered.lot == "__single__"


def test_recover_returns_none_when_no_candidate_validates() -> None:
    text = '{"expected_total": 100.0}\n{"unrelated": true}'
    recovered = recover_validated_payload(
        text, _MiniScope, skip_payload={"expected_total": 100.0},
    )
    assert recovered is None


def test_recover_returns_none_for_empty_raw_text() -> None:
    assert recover_validated_payload("", _MiniScope) is None


def test_recover_real_full_candidate_scope_after_prompt_echo() -> None:
    """ 真实场景: 回显评分基准常量后输出完整 FullCandidateScope。"""
    full_scope = {
        "scheme": "scheme_1",
        "lot": "__single__",
        "scoring_basis": {"expected_total": 100.0},
        "facts": [
            {
                "category": "TECH",
                "score_item_name": "总体设计方案",
                "description": "总体设计思路完整清晰",
                "score_weight": 15.0,
                "fact_type": "base_score",
                "volume": "technical",
                "volume_label": "技术标",
                "score_rules": ["根据方案完整性酌情评分"],
                "deduction_rules": [],
                "evidence_refs": [
                    {
                        "source_chunk_id": 12345,
                        "source_page": 25,
                        "evidence_quote": "总体设计方案（15分）",
                        "supports_fields": ["score_item_name", "score_weight"],
                    }
                ],
            }
        ],
        "declared_total": 100.0,
    }
    text = (
        '根据评分基准 {"expected_total": 100.0} 提取如下:\n'
        + json.dumps(full_scope, ensure_ascii=False)
    )
    recovered = recover_validated_payload(
        text, FullCandidateScope, skip_payload={"expected_total": 100.0},
    )
    assert recovered is not None
    assert recovered.scheme == "scheme_1"
    assert len(recovered.facts) == 1
    assert recovered.facts[0].score_weight == 15.0
