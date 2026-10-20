"""串标项目级事实段拼装器单测

覆盖 `core/collusion/narrative_builder.py` 的:
- build_factual_explanation 主入口的多场景输出
- _describe_coverage_brief / _describe_pair_degradations /
 _describe_industry_profile_state / _describe_tender_context 四个 helper

关键风险点（来自 根因分析）:
- 白名单命中类别必须出现在事实段（不可被压缩成"白名单命中"四个字）
- 行业画像 status 四态必须输出**截然不同**的文案
- LLM raw 值（degradation_reason.raw_value）必须出现在事实段
- 空输入边界（无 pair_results / industry_profile=None / coverage=0）
"""

from __future__ import annotations

from app.core.collusion.narrative_builder import (
    _describe_coverage_brief,
    _describe_industry_profile_state,
    _describe_pair_degradations,
    _describe_tender_context,
    build_factual_explanation,
    build_factual_explanation_items,
)

# ---------------------------------------------------------------------------
# 工具：构造可控的输入字典
# ---------------------------------------------------------------------------


def _make_coverage(
    *,
    total: int = 1,
    analyzed: int = 1,
    clear: int = 0,
    suspicious: int = 0,
    review_required: int = 0,
    failed: int = 0,
    ratio: float = 1.0,
) -> dict:
    return {
        "total_pairs": total,
        "analyzed_pairs": analyzed,
        "clear_pairs": clear,
        "suspicious_pairs": suspicious,
        "review_required_pairs": review_required,
        "failed_pairs": failed,
        "coverage_ratio": ratio,
    }


def _make_pair(
    *,
    a_name: str = "投标A.pdf",
    b_name: str = "投标B.pdf",
    decision_state: str = "decidable",
    risk_level: str = "none",
    quality_flags: list[str] | None = None,
    whitelist_categories: list[str] | None = None,
    degradation_reason: dict | None = None,
) -> dict:
    return {
        "pair_ref": {
            "doc_a_id": 1,
            "doc_b_id": 2,
            "doc_a_name": a_name,
            "doc_b_name": b_name,
        },
        "decision_state": decision_state,
        "risk_level": risk_level,
        "overall_risk": risk_level,
        "quality_flags": quality_flags or [],
        "whitelist_categories": whitelist_categories or [],
        "degradation_reason": degradation_reason or {},
    }


# ---------------------------------------------------------------------------
# 主入口 build_factual_explanation
# ---------------------------------------------------------------------------


class TestBuildFactualExplanationMainCase:
    """与用户报问题的场景一致：1 对文件、白名单命中、风险枚举异常、行业画像未知。"""

    def test_user_reported_scenario_produces_concrete_details(self):
        """复现用户报的场景：必须显式输出白名单类别 + LLM raw 值 + 行业画像状态"""
        pair = _make_pair(
            a_name="CK投标2.pdf",
            b_name="BJ投标1.pdf",
            decision_state="needs_review",
            risk_level="inconclusive",
            quality_flags=["（运维内部信号，由 narrative_builder 统一翻译）"],
            whitelist_categories=["工程类通用章节", "法规/固定法律条文"],
            degradation_reason={
                "type": "invalid_risk_enum",
                "raw_value": "uncertain",
                "fallback": "needs_review",
            },
        )
        result = build_factual_explanation(
            coverage_stats=_make_coverage(
                total=1, analyzed=1, review_required=1, ratio=1.0
            ),
            pair_results=[pair],
            industry_profile={
                "status": "llm_failed",
                "failure_reason": "timeout",
                "confidence": "unknown",
            },
            has_tender_doc=False,
        )
        assert result is not None
        # 覆盖率信息
        assert "1 对" in result
        assert "100%" in result
        # 白名单类别必须直显（不可被压缩）
        assert "工程类通用章节" in result
        assert "法规/固定法律条文" in result
        # LLM raw 值必须直显
        assert "uncertain" in result
        # 行业画像失败原因必须直显
        assert "timeout" in result
        # 未上传招标参照提示
        assert "招标文件" in result

    def test_user_reported_scenario_produces_structured_items(self):
        """：事实说明必须保留条目语义，供 UI 编号渲染。"""
        pair = _make_pair(
            a_name="BJ投标1.pdf",
            b_name="CK投标2.pdf",
            decision_state="needs_review",
            whitelist_categories=["通用格式模板"],
            degradation_reason={
                "type": "inconclusive_insufficient_evidence",
                "raw_value": "inconclusive",
                "fallback": "needs_review",
            },
        )

        items = build_factual_explanation_items(
            coverage_stats=_make_coverage(
                total=1, analyzed=1, review_required=1, ratio=1.0
            ),
            pair_results=[pair],
            industry_profile={
                "status": "llm_returned_unknown",
                "confidence": "unknown",
            },
            has_tender_doc=False,
        )

        assert len(items) == 3
        assert items[0].startswith("本次共完成 1 对文件交叉比对")
        assert "BJ投标1.pdf × CK投标2.pdf" in items[1]
        assert "行业画像" in items[2]
        assert all("未上传招标文件" not in item for item in items)

    def test_all_clear_no_signals_returns_none_or_minimal(self):
        """全部正常：无降级、无行业画像异常、有招标参照 → 应返回 None 或最简"""
        pair = _make_pair(
            decision_state="decidable",
            risk_level="none",
        )
        result = build_factual_explanation(
            coverage_stats=_make_coverage(total=3, analyzed=3, clear=3, ratio=1.0),
            pair_results=[pair],
            industry_profile={"status": "success", "confidence": "high"},
            has_tender_doc=True,
        )
        # 仅覆盖率简述行，无降级/行业/招标信号
        assert result is not None
        assert "3 对" in result
        assert "工程类通用章节" not in result  # 无白名单命中
        assert "AI 调用失败" not in result

    def test_empty_input_returns_none(self):
        """所有信号均空 → 返回 None"""
        result = build_factual_explanation(
            coverage_stats={},
            pair_results=[],
            industry_profile={"status": "success"},
            has_tender_doc=True,
        )
        assert result is None

    def test_industry_profile_none_falls_back_to_llm_failed(self):
        """industry_profile=None 视同 status=llm_failed（与契约一致）"""
        result = build_factual_explanation(
            coverage_stats=_make_coverage(total=2, analyzed=2),
            pair_results=[],
            industry_profile=None,
            has_tender_doc=True,
        )
        assert result is not None
        assert "AI 调用失败" in result or "保守判定路径" in result


# ---------------------------------------------------------------------------
# Helper: _describe_coverage_brief
# ---------------------------------------------------------------------------


class TestDescribeCoverageBrief:
    def test_renders_total_analyzed_and_ratio(self):
        out = _describe_coverage_brief(
            _make_coverage(total=5, analyzed=4, clear=3, suspicious=1, ratio=0.8)
        )
        assert "4 对" in out
        assert "80%" in out
        assert "3 对未发现异常" in out
        assert "1 对存在异常证据" in out

    def test_includes_pending_count(self):
        out = _describe_coverage_brief(
            _make_coverage(total=3, analyzed=3, review_required=1, failed=1)
        )
        assert "2 对已转人工复核或分析失败" in out

    def test_returns_empty_when_no_pairs(self):
        out = _describe_coverage_brief({"total_pairs": 0, "analyzed_pairs": 0})
        assert out == ""

    def test_handles_invalid_input(self):
        assert _describe_coverage_brief({}) == ""
        assert _describe_coverage_brief(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helper: _describe_pair_degradations
# ---------------------------------------------------------------------------


class TestDescribePairDegradations:
    def test_whitelist_categories_appear_verbatim(self):
        pair = _make_pair(
            whitelist_categories=["IT服务类通用章节"],
            decision_state="needs_review",
        )
        lines = _describe_pair_degradations([pair])
        assert len(lines) == 1
        assert "IT服务类通用章节" in lines[0]
        assert "投标A.pdf" in lines[0]
        assert "投标B.pdf" in lines[0]

    def test_invalid_risk_enum_raw_value_appears(self):
        pair = _make_pair(
            decision_state="needs_review",
            degradation_reason={
                "type": "invalid_risk_enum",
                "raw_value": "very_high_lol",
                "fallback": "needs_review",
            },
        )
        lines = _describe_pair_degradations([pair])
        assert len(lines) == 1
        assert "very_high_lol" in lines[0]

    def test_inconclusive_insufficient_evidence_clear_wording(self):
        """ 对齐：inconclusive 降级文案必须中性，禁止出现'未返回有效风险等级'。"""
        pair = _make_pair(
            decision_state="needs_review",
            degradation_reason={
                "type": "inconclusive_insufficient_evidence",
                "raw_value": "inconclusive",
                "fallback": "needs_review",
            },
        )
        lines = _describe_pair_degradations([pair])
        assert len(lines) == 1
        assert "证据不足" in lines[0]
        assert "未返回有效风险等级" not in lines[0]
        # 不应把内部枚举 inconclusive 字面塞进用户文案
        assert "inconclusive" not in lines[0]

    def test_clean_pair_with_no_signals_skipped(self):
        pair = _make_pair(decision_state="decidable", risk_level="none")
        assert _describe_pair_degradations([pair]) == []

    def test_failed_pair_without_reason_has_fallback_text(self):
        pair = _make_pair(decision_state="failed")
        lines = _describe_pair_degradations([pair])
        assert len(lines) == 1
        assert "失败" in lines[0]

    def test_multiple_pairs_independent_lines(self):
        pair_a = _make_pair(
            a_name="X.pdf", b_name="Y.pdf",
            decision_state="needs_review",
            whitelist_categories=["通用格式模板"],
        )
        pair_b = _make_pair(
            a_name="M.pdf", b_name="N.pdf",
            decision_state="failed",
        )
        lines = _describe_pair_degradations([pair_a, pair_b])
        assert len(lines) == 2

    def test_skips_non_dict_entries(self):
        assert _describe_pair_degradations([None, "garbage", 42]) == []  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# Helper: _describe_industry_profile_state
# ---------------------------------------------------------------------------


class TestDescribeIndustryProfileState:
    def test_success_status_returns_empty(self):
        assert _describe_industry_profile_state(
            {"status": "success", "confidence": "high"}
        ) == ""

    def test_llm_failed_includes_failure_reason(self):
        out = _describe_industry_profile_state(
            {"status": "llm_failed", "failure_reason": "exception:ValueError"}
        )
        assert "AI 调用失败" in out
        assert "exception:ValueError" in out

    def test_insufficient_signals_message_distinct(self):
        out_insuf = _describe_industry_profile_state(
            {"status": "insufficient_signals", "signal_count": 1}
        )
        out_unknown = _describe_industry_profile_state(
            {"status": "llm_returned_unknown"}
        )
        assert out_insuf != out_unknown
        assert "信号" in out_insuf

    def test_none_profile_treated_as_llm_failed(self):
        out = _describe_industry_profile_state(None)
        assert "保守判定路径" in out

    def test_old_snapshot_without_status_inferred(self):
        """兼容旧数据：无 status 字段时按 signals/confidence 推断"""
        out = _describe_industry_profile_state(
            {"industry_type": "unknown", "confidence": "low", "signals": []}
        )
        # signals=0 → insufficient_signals 文案
        assert "信号" in out

    def test_unknown_status_falls_back(self):
        out = _describe_industry_profile_state({"status": "future_unknown_status_v9"})
        assert "状态异常" in out


# ---------------------------------------------------------------------------
# Helper: _describe_tender_context
# ---------------------------------------------------------------------------


class TestDescribeTenderContext:
    def test_with_tender_returns_empty(self):
        assert _describe_tender_context(True) == ""

    def test_without_tender_returns_hint(self):
        out = _describe_tender_context(False)
        assert "招标文件" in out
        assert "模板" in out
