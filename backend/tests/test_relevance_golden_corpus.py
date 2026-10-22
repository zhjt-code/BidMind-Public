""" golden 语料回归: 可评审性判定（项目1527真实样例）。

复用 golden corpus 模式: 把真实误判样本 + 期望 LLM 载体判定固化为 fixture，
锁定合成层(_decide_updates 优先级/双确认/例外防误杀/占位符规则)的最终输出。
LLM 判定本身不可确定性回归，故 fixture 的 llm 字段作为合成输入；
后续每出现一次线上误判，按同格式追加 case，使失败沉淀为护栏而非消失。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from app.core.extractor.relevance.relevance_classifier import (  # noqa: E402
    classify_review_relevance,
)
from app.schemas.requirement import ExtractedRequirement  # noqa: E402

_CORPUS_PATH = Path(__file__).parent / "fixtures" / "relevance" / "golden_corpus.json"
_CORPUS = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))


class FakeLLM:
    def __init__(self, scripted):
        self.scripted = list(scripted)

    async def chat_json(self, **kwargs):
        result = self.scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _build_item(case: dict) -> ExtractedRequirement:
    return ExtractedRequirement(
        category=case["category"],
        description=case["description"],
        is_mandatory=bool(case["is_mandatory"]),
    )


@pytest.mark.parametrize("case", _CORPUS["cases"], ids=lambda c: c["id"])
def test_golden_case_synthesis(case: dict):
    """给定期望 LLM 载体判定，合成后的最终评审意义必须与 golden 期望一致。"""
    items = [_build_item(case)]
    llm_verdict = dict(case["llm"])
    llm_verdict["idx"] = 0
    llm = FakeLLM([[llm_verdict]])

    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))

    assert out[0].review_relevance == case["expected_relevance"], (
        f"golden case {case['id']} 合成结果漂移: "
        f"got={out[0].review_relevance} expected={case['expected_relevance']}"
    )
    if case.get("expected_is_key_review"):
        assert out[0].is_key_review is True, f"golden case {case['id']} 评分项铁律失效"
    # 否决语义必须原样保留(折叠仅改评审意义, 供前端风险分组展示)
    assert out[0].is_mandatory == bool(case["is_mandatory"])


def test_golden_mandatory_cases_stay_actionable_without_llm():
    """LLM 失败保守回退: 全部否决样例在无 LLM 翼时一律保持 actionable(不做单翼降级)。"""
    mandatory_cases = [c for c in _CORPUS["cases"] if c["is_mandatory"]]
    assert mandatory_cases, "语料必须包含否决样例"
    items = [_build_item(c) for c in mandatory_cases]
    llm = FakeLLM([RuntimeError("llm down")])

    out = asyncio.run(classify_review_relevance(items, llm, batch_size=50))

    for case, item in zip(mandatory_cases, out):
        assert item.review_relevance == "actionable", (
            f"golden case {case['id']}: LLM 失败时否决项被单翼降级(违反保守回退)"
        )


def test_golden_corpus_covers_1527_pain_points():
    """语料完整性护栏: 必须覆盖 1527 报告的核心痛点类型。"""
    ids = {c["id"] for c in _CORPUS["cases"]}
    required = {
        "behavioral_ban_gua_kao",          # 挂靠/转包/行贿(既有词表缺口)
        "contract_template_placeholder",   # 占位符合同模板
        "duration_commitment_reviewable",  # 可评审工期(防过度折叠反例)
        "scoring_item_iron_rule",          # 评分项铁律
    }
    missing = required - ids
    assert not missing, f"golden 语料缺少关键痛点用例: {missing}"
