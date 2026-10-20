"""串标链路 input-too-long 400 兜底测试。"""

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.collusion.analyzer import CollusionAnalyzer
from app.core.collusion.industry_profiler import CollusionIndustryProfiler


class _FakeValidated:
    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self) -> dict:
        return self._payload


class _FakeValidationClient:
    def __init__(self, scripted_results):
        self.scripted_results = list(scripted_results)
        self.calls = []

    async def chat_with_validation(self, **kwargs):
        self.calls.append(kwargs)
        current = self.scripted_results.pop(0)
        if isinstance(current, Exception):
            raise current
        return _FakeValidated(current)


def _make_api_status_error(message: str, status_code: int = 400) -> APIStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status_code=status_code, request=request)
    return APIStatusError(message, response=response, body={"error": {"message": message}})


@pytest.mark.asyncio
async def test_collusion_text_dims_retries_after_input_too_long_400():
    fake_client = _FakeValidationClient([
        _make_api_status_error("Range of input length should be [1, 30720]"),
        {
            "d3_text": {"score": 65},
            "d6_format": {"score": 20},
            "d7_abnormal": {"score": 10},
        },
    ])
    analyzer = object.__new__(CollusionAnalyzer)
    analyzer._client = fake_client
    analyzer._project_id = 123
    analyzer._last_heartbeat_stage = "test"
    analyzer._last_heartbeat_progress = "testing"
    analyzer._last_heartbeat_extra = {}
    # object.__new__ 跳过了 __init__，需手动补全 _call_llm_with_input_guard 链路所需属性
    analyzer._cancel_check = None
    analyzer._kb_usage_loaded = False

    doc_a = SimpleNamespace(id=1, filename="A.pdf")
    doc_b = SimpleNamespace(id=2, filename="B.pdf")
    similar_chunks = [
        {"text_a": "技术方案内容A" * 40, "text_b": "技术方案内容B" * 40, "score": 0.95, "page_a": 1, "page_b": 1},
        {"text_a": "管理措施A" * 40, "text_b": "管理措施B" * 40, "score": 0.9, "page_a": 2, "page_b": 2},
        {"text_a": "保障内容A" * 40, "text_b": "保障内容B" * 40, "score": 0.85, "page_a": 3, "page_b": 3},
        {"text_a": "补充条款A" * 40, "text_b": "补充条款B" * 40, "score": 0.8, "page_a": 4, "page_b": 4},
    ]

    result = await analyzer._analyze_text_dims(
        doc_a=doc_a,
        doc_b=doc_b,
        similar_chunks=similar_chunks,
        similarity_score=0.88,
        tender_format={"rules": ["固定格式要求"]},
        industry_profile={"confidence": "high", "industry_type": "电力", "standardized_domains": ["封面"], "creative_domains": ["实施方案"]},
        coverage_pct="80",
        max_continuous_span="1200",
    )

    assert result["d3_text"]["score"] == 65
    assert len(fake_client.calls) == 2
    assert len(fake_client.calls[1]["user_message"]) < len(fake_client.calls[0]["user_message"])


@pytest.mark.asyncio
async def test_collusion_industry_profiler_retries_signal_stage_on_input_too_long():
    fake_client = _FakeValidationClient([
        _make_api_status_error("Range of input length should be [1, 30720]"),
        {"signals": ["电力"], "confidence": "low"},
    ])
    profiler = object.__new__(CollusionIndustryProfiler)
    profiler._project_id = 123
    profiler._client = fake_client

    profile = await profiler.generate(
        tender_text="招标要求" * 2000,
        bid_extractions=[
            {
                "technical_highlights": ["技术特点" * 20] * 10,
                "qualifications": ["资质证书" * 20] * 10,
                "past_projects": ["历史项目" * 20] * 10,
                "company_info": {"company_name": "甲公司"},
            }
        ],
    )

    assert profile is not None
    assert profile["industry_type"] == "unknown"
    assert len(fake_client.calls) == 2
    assert len(fake_client.calls[1]["user_message"]) < len(fake_client.calls[0]["user_message"])


@pytest.mark.asyncio
async def test_collusion_run_synthesis_retries_after_input_too_long():
    fake_client = _FakeValidationClient([
        _make_api_status_error("Range of input length should be [1, 30720]"),
        {
            "overall_risk": "medium",
            "suspected_groups": [],
            "collusion_network": None,
            "bid_accompanying_pattern": None,
            "summary": "综合结论",
            "investigation_suggestions": ["人工复核"],
        },
    ])
    analyzer = object.__new__(CollusionAnalyzer)
    analyzer._client = fake_client
    analyzer._project_id = 123
    analyzer._last_heartbeat_stage = "test"
    analyzer._last_heartbeat_progress = "testing"
    analyzer._last_heartbeat_extra = {}
    # object.__new__ 跳过了 __init__，需手动补全 _call_llm_with_input_guard 链路所需属性
    analyzer._cancel_check = None

    pair_results = []
    for idx in range(6):
        pair_results.append({
            "pair_id": f"pair-{idx}",
            "pair_ref": {
                "doc_a_id": idx,
                "doc_b_id": idx + 10,
                "doc_a_name": f"A{idx}.pdf",
                "doc_b_name": f"B{idx}.pdf",
            },
            "risk_level": "medium",
            "overall_risk": "medium" if idx < 4 else "low",
            "analysis_state": "done",
            "decision_state": "done",
            "quality_flags": ["covered"],
            "key_findings": [
                {"summary": "发现一"},
                {"summary": "发现二"},
                {"summary": "发现三"},
            ],
        })

    result = await analyzer._run_synthesis(
        bid_docs=[SimpleNamespace(id=1, filename="A.pdf"), SimpleNamespace(id=2, filename="B.pdf")],
        pair_results=pair_results,
        has_tender_doc=True,
        total_pairs=6,
        llm_pair_count=6,
        skipped_count=0,
        industry_profile={"confidence": "high", "industry_type": "电力"},
    )

    assert result["overall_risk"] == "medium"
    assert len(fake_client.calls) == 2
    assert len(fake_client.calls[1]["user_message"]) < len(fake_client.calls[0]["user_message"])
