"""串标行业画像 status 四态契约测试

覆盖 industry_profiler.generate 改造后的四种 status：
- success: LLM 正常归纳出具体行业
- llm_returned_unknown: LLM 成功但归纳为 unknown
- insufficient_signals: 空信号或弱信号被强制降级
- llm_failed: LLM 调用超时或抛异常

同时验证 `_apply_profile_confidence_to_candidate_scores` 与
`_build_profile_quality_flags` 在新 status 下的兼容性回填。

测试策略：
- generate 链路通过 mock `_client.chat_with_validation` 直接注入 LLMResult
- 不做真实 LLM/HTTP/数据库调用
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# 工具：构造可控的 CollusionIndustryProfiler 实例
# ---------------------------------------------------------------------------


def _build_profiler():
    from app.core.collusion.industry_profiler import CollusionIndustryProfiler

    # 阻断 get_review_client 的真实初始化（避免触发配置加载）
    with patch(
        "app.core.collusion.industry_profiler.get_review_client",
        return_value=SimpleNamespace(),
    ):
        return CollusionIndustryProfiler(project_id=999)


def _make_signal_result(signals: list[dict], confidence: str = "medium"):
    """构造 LLMIndustrySignalResult-like 对象（mock 输出）。"""
    payload = {
        "industry_keywords": [],
        "technical_terms": [],
        "qualification_signals": [],
        "signals": signals,
        "confidence": confidence,
    }
    return SimpleNamespace(model_dump=lambda: payload)


def _make_profile_result(industry_type: str, confidence: str = "medium"):
    """构造 LLMIndustryProfileResult-like 对象（mock 输出）。"""
    payload = {
        "industry_type": industry_type,
        "confidence": confidence,
        "summary": f"基于材料归纳的行业类型: {industry_type}",
        "standardized_domains": [],
        "creative_domains": [],
        "common_qualification_types": [],
        "common_pricing_patterns": [],
    }
    return SimpleNamespace(model_dump=lambda: payload)


# ---------------------------------------------------------------------------
# 四种 status 各一例
# ---------------------------------------------------------------------------


class TestIndustryProfileStatusSuccess:
    """LLM 正常归纳出具体行业 → status=success"""

    def test_status_is_success_when_industry_resolved(self):
        profiler = _build_profiler()
        signals = [
            {"category": "industry_keyword", "evidence": "石油"},
            {"category": "technical_term", "evidence": "输油管线"},
        ]
        signal_mock = AsyncMock(return_value=_make_signal_result(signals, "high"))
        profile_mock = AsyncMock(
            return_value=_make_profile_result("石油工程-管线改造", "high")
        )

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ), patch.object(
                profiler._client, "chat_with_validation", new=profile_mock, create=True
            ):
                return await profiler.generate(
                    tender_text="石油库区改造工程", bid_extractions=[]
                )

        result = asyncio.run(run())
        assert result is not None
        assert result["status"] == "success"
        assert result["failure_reason"] is None
        assert result["industry_type"] == "石油工程-管线改造"
        assert result["signal_count"] == 2


class TestIndustryProfileStatusLLMReturnedUnknown:
    """LLM 成功但归纳为 unknown → status=llm_returned_unknown"""

    def test_status_is_llm_returned_unknown_when_industry_type_unknown(self):
        profiler = _build_profiler()
        signals = [
            {"category": "industry_keyword", "evidence": "杂项A"},
            {"category": "industry_keyword", "evidence": "杂项B"},
        ]
        signal_mock = AsyncMock(return_value=_make_signal_result(signals, "low"))
        profile_mock = AsyncMock(
            return_value=_make_profile_result("unknown", "unknown")
        )

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ), patch.object(
                profiler._client, "chat_with_validation", new=profile_mock, create=True
            ):
                return await profiler.generate(tender_text=None, bid_extractions=[])

        result = asyncio.run(run())
        assert result["status"] == "llm_returned_unknown"
        assert result["industry_type"] == "unknown"
        assert result["failure_reason"] is None
        assert result["signal_count"] == 2

    def test_status_is_llm_returned_unknown_when_industry_type_empty(self):
        """边界：LLM 返回空字符串也算 llm_returned_unknown"""
        profiler = _build_profiler()
        signals = [
            {"category": "industry_keyword", "evidence": "x"},
            {"category": "industry_keyword", "evidence": "y"},
        ]
        signal_mock = AsyncMock(return_value=_make_signal_result(signals, "low"))
        profile_mock = AsyncMock(return_value=_make_profile_result("", "low"))

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ), patch.object(
                profiler._client, "chat_with_validation", new=profile_mock, create=True
            ):
                return await profiler.generate(tender_text=None, bid_extractions=[])

        result = asyncio.run(run())
        assert result["status"] == "llm_returned_unknown"


class TestIndustryProfileStatusSignalAdequacy:
    """按信号强度判定是否进入第二轮画像归纳"""

    def test_single_strong_signal_enters_profile_generation(self):
        profiler = _build_profiler()
        signals = [
            {
                "signal_type": "domain",
                "value": "医院信息化系统集成采购",
                "source": "tender",
                "source_refs": ["招标文件行业相关片段1"],
                "confidence": 0.9,
            }
        ]
        signal_mock = AsyncMock(return_value=_make_signal_result(signals, "low"))
        profile_mock = AsyncMock(
            return_value=_make_profile_result("医院信息化-系统集成", "high")
        )

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ), patch.object(
                profiler._client, "chat_with_validation", new=profile_mock, create=True
            ):
                return await profiler.generate(tender_text=None, bid_extractions=[])

        result = asyncio.run(run())
        assert result["status"] == "success"
        assert result["industry_type"] == "医院信息化-系统集成"
        # 单强信号仅允许中置信，避免把单点证据放大为高置信行业先验。
        assert result["confidence"] == "medium"
        assert result["signal_count"] == 1
        assert result["failure_reason"] is None
        assert profile_mock.await_count == 1

    def test_status_insufficient_signals_when_only_one_weak_signal(self):
        profiler = _build_profiler()
        signals = [{"signal_type": "term", "value": "服务", "confidence": 0.4}]
        signal_mock = AsyncMock(return_value=_make_signal_result(signals, "low"))
        profile_mock = AsyncMock(side_effect=AssertionError("不应调用第二轮 LLM"))

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ), patch.object(
                profiler._client, "chat_with_validation", new=profile_mock, create=True
            ):
                return await profiler.generate(tender_text=None, bid_extractions=[])

        result = asyncio.run(run())
        assert result["status"] == "insufficient_signals"
        assert result["industry_type"] == "unknown"
        assert result["signal_count"] == 1
        assert result["failure_reason"] == "weak_signals"

    def test_status_insufficient_signals_when_zero_signals(self):
        profiler = _build_profiler()
        signal_mock = AsyncMock(return_value=_make_signal_result([], "unknown"))
        profile_mock = AsyncMock(side_effect=AssertionError("不应调用第二轮 LLM"))

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ), patch.object(
                profiler._client, "chat_with_validation", new=profile_mock, create=True
            ):
                return await profiler.generate(tender_text=None, bid_extractions=[])

        result = asyncio.run(run())
        assert result["status"] == "insufficient_signals"
        assert result["signal_count"] == 0

    def test_empty_llm_signals_use_structured_context_fallback(self):
        """: 结构化投标信号明确时，LLM 空 signals 不应直接导致画像降级。"""
        profiler = _build_profiler()
        signal_mock = AsyncMock(return_value=_make_signal_result([], "unknown"))
        profile_mock = AsyncMock(
            return_value=_make_profile_result("石油天然气-科研服务", "high")
        )

        bid_extractions = [
            {
                "technical_highlights": [
                    "油气集输用新型低碳低铬系耐蚀管线钢的开发",
                    "压裂施工参数对应力场变化的影响分析",
                ],
                "qualifications": [
                    "石油天然气勘探开发科研服务 HBKJZR1007-202506",
                ],
                "company_info": {"company_name": "<ORG_7>"},
            }
        ]

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ), patch.object(
                profiler._client, "chat_with_validation", new=profile_mock, create=True
            ):
                return await profiler.generate(tender_text=None, bid_extractions=bid_extractions)

        result = asyncio.run(run())
        assert result["status"] == "success"
        assert result["industry_type"] == "石油天然气-科研服务"
        assert result["signal_count"] >= 1
        assert any("油气" in str(signal) or "石油天然气" in str(signal) for signal in result["signals"])
        assert profile_mock.await_count == 1
        profile_user_message = profile_mock.await_args.kwargs["user_message"]
        assert "source_context_excerpt" in profile_user_message
        assert "油气集输" in profile_user_message

    def test_llm_unknown_with_strong_signals_uses_deterministic_profile_fallback(self):
        """: 强信号充分时，画像 LLM 返回 unknown 不能继续展示行业先验缺失。"""
        profiler = _build_profiler()
        signals = [
            {
                "signal_type": "process",
                "value": "油气集输用新型低碳低铬系耐蚀管线钢的开发",
                "source": "bid_extraction:technical_highlights",
                "source_refs": ["bid_extraction:technical_highlights"],
                "confidence": 0.9,
            },
            {
                "signal_type": "qualification",
                "value": "石油天然气勘探开发科研服务 HBKJZR1007-202506",
                "source": "bid_extraction:qualifications",
                "source_refs": ["bid_extraction:qualifications"],
                "confidence": 0.9,
            },
        ]
        signal_mock = AsyncMock(return_value=_make_signal_result(signals, "high"))
        profile_mock = AsyncMock(return_value=_make_profile_result("unknown", "unknown"))

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ), patch.object(
                profiler._client, "chat_with_validation", new=profile_mock, create=True
            ):
                return await profiler.generate(tender_text=None, bid_extractions=[])

        result = asyncio.run(run())
        assert result["status"] == "success"
        assert result["industry_type"] != "unknown"
        assert "石油" in result["industry_type"] or "油气" in result["industry_type"]
        assert result["source_mode"] == "deterministic_fallback"
        assert result["failure_reason"] == "llm_returned_unknown"


class TestIndustryProfileStatusLLMFailed:
    """LLM 超时/异常 → status=llm_failed + failure_reason"""

    def test_status_llm_failed_on_timeout(self):
        profiler = _build_profiler()
        signal_mock = AsyncMock(side_effect=asyncio.TimeoutError())

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ):
                return await profiler.generate(tender_text=None, bid_extractions=[])

        result = asyncio.run(run())
        assert result["status"] == "llm_failed"
        assert result["failure_reason"] == "timeout"
        assert result["industry_type"] == "unknown"
        assert result["signal_count"] == 0

    def test_status_llm_failed_on_exception(self):
        profiler = _build_profiler()
        signal_mock = AsyncMock(side_effect=ValueError("invalid response"))

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ):
                return await profiler.generate(tender_text=None, bid_extractions=[])

        result = asyncio.run(run())
        assert result["status"] == "llm_failed"
        assert result["failure_reason"] == "exception:ValueError"

    def test_failed_profile_never_returns_none(self):
        """关键契约：generate 不再返回 None，下游可以无条件访问 status"""
        profiler = _build_profiler()
        signal_mock = AsyncMock(side_effect=RuntimeError("oops"))

        async def run():
            with patch(
                "app.core.collusion.industry_profiler.retry_on_input_too_long_400",
                new=signal_mock,
            ):
                return await profiler.generate(tender_text=None, bid_extractions=[])

        result = asyncio.run(run())
        assert result is not None
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 下游消费方契约
# ---------------------------------------------------------------------------


class TestApplyProfileConfidenceToCandidateScores:
    """`_apply_profile_confidence_to_candidate_scores` 必须兼容四种 status"""

    @staticmethod
    def _apply(profile):
        from app.core.collusion.analyzer import (
            _apply_profile_confidence_to_candidate_scores,
        )
        scores = {(1, 2): 0.8}
        return _apply_profile_confidence_to_candidate_scores(
            scores, industry_profile=profile
        )

    def test_success_with_high_confidence_keeps_score(self):
        out = self._apply({"status": "success", "confidence": "high"})
        assert out[(1, 2)] == 0.8

    def test_llm_failed_degrades_to_unknown_factor(self):
        out = self._apply(
            {"status": "llm_failed", "confidence": "unknown", "failure_reason": "timeout"}
        )
        # unknown 系数 0.8 → 0.8 * 0.8 = 0.64
        assert out[(1, 2)] == pytest.approx(0.64, abs=0.001)

    def test_insufficient_signals_uses_low_factor(self):
        out = self._apply(
            {"status": "insufficient_signals", "confidence": "low", "signal_count": 1}
        )
        # low 系数 0.85
        assert out[(1, 2)] == pytest.approx(0.68, abs=0.001)

    def test_none_profile_treated_as_unknown(self):
        out = self._apply(None)
        assert out[(1, 2)] == pytest.approx(0.64, abs=0.001)


class TestBuildProfileQualityFlags:
    """`_build_profile_quality_flags` 按 status 四分支输出用户态文案"""

    @staticmethod
    def _build(profile):
        from app.core.collusion.analyzer import _build_profile_quality_flags
        return _build_profile_quality_flags(profile)

    def test_success_returns_empty(self):
        assert self._build({"status": "success", "confidence": "high"}) == []

    def test_llm_failed_includes_failure_reason(self):
        flags = self._build(
            {"status": "llm_failed", "failure_reason": "timeout"}
        )
        assert len(flags) == 1
        assert "AI 调用" in flags[0]
        assert "timeout" in flags[0]
        assert "保守判定路径" in flags[0]

    def test_insufficient_signals_includes_signal_count(self):
        flags = self._build({"status": "insufficient_signals", "signal_count": 1})
        assert len(flags) == 1
        assert "信号" in flags[0]
        assert "1" in flags[0]

    def test_llm_returned_unknown_distinct_from_llm_failed(self):
        flags_unknown = self._build({"status": "llm_returned_unknown"})
        flags_failed = self._build({"status": "llm_failed"})
        assert flags_unknown != flags_failed
        assert any("未能归纳" in f for f in flags_unknown)

    def test_none_profile_falls_back_to_llm_failed_narrative(self):
        flags = self._build(None)
        assert len(flags) == 1
        assert "保守判定路径" in flags[0]

    def test_old_snapshot_without_status_inferred_from_signals(self):
        """兼容旧数据：无 status 字段时按 confidence/industry_type 推断"""
        flags = self._build(
            {"industry_type": "unknown", "confidence": "low", "signals": [{"x": 1}]}
        )
        # signal_count=1 → 推断为 insufficient_signals
        assert len(flags) == 1
        assert "信号" in flags[0]
