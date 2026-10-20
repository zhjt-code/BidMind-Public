"""串标修复 P1 阶段单测

覆盖 P1-1/P1-2/P1-3/P1-4 的核心行为：
- P1-1 `_algorithm_d4_score`：精确比例/尾数/完全相等 + 缺数据 early-return
- P1-1 `_get_algorithm_d4_hits`：报价命中集合构造
- P1-1 `get_smart_filter_pairs(include_pricing_patterns=True)`：pricing_patterns 纳入 must_llm
- P1-2 `_run_overall_synthesis` 使用 synthesis_timeout（静态检查：import 与字面量）
- P1-3 `build_industry_signal_context` 信号源扩充
- P1-4 `_simple_synthesis` 参数冗余删除 + 兜底 suspected_groups 降序生成
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# P1-1 _algorithm_d4_score
# ---------------------------------------------------------------------------

class TestAlgorithmD4Score:
    @staticmethod
    def _call(price_a, price_b):
        from app.core.collusion.analyzer import _algorithm_d4_score
        return _algorithm_d4_score(
            {"price_info": {"total_price": price_a}},
            {"price_info": {"total_price": price_b}},
            "文件A", "文件B",
        )

    def test_exact_match_score_1(self):
        score, findings = self._call("1000000", "1000000")
        assert score == 1.0
        assert any("完全相同" in f["summary"] for f in findings)

    def test_precise_ratio_score_0_8(self):
        # 0.9 落在 [0.85, 1.0)
        score, findings = self._call("900000", "1000000")
        assert score >= 0.8
        assert any("比例" in f["summary"] for f in findings)

    def test_tail_digits_match_score_0_5(self):
        # 尾数都是 50，比例超出 [0.85,1.0)
        score, findings = self._call("100050", "200050")
        assert 0.4 <= score <= 0.6
        assert any("尾数" in f["summary"] for f in findings)

    def test_missing_price_returns_zero(self):
        score, findings = self._call(None, "1000000")
        assert score == 0.0
        assert findings == []

    def test_unparseable_price_returns_zero(self):
        score, findings = self._call("面议", "1000000")
        assert score == 0.0

    def test_zero_or_negative_returns_zero(self):
        score, findings = self._call("0", "1000000")
        assert score == 0.0

    def test_with_currency_unit_万(self):
        # 100 万 vs 95 万 ≈ 95%，应触发比例命中
        score, findings = self._call("100万元", "95万")
        assert score >= 0.8

    def test_large_ratio_below_threshold_not_hit_by_ratio(self):
        # 比例 0.5，不在 [0.85, 1.0) 内，不应命中比例；且尾数不同→score=0
        score, _ = self._call("500000", "1000000")
        assert score == 0.0


# ---------------------------------------------------------------------------
# P1-1 _get_algorithm_d4_hits
# ---------------------------------------------------------------------------

class TestGetAlgorithmD4Hits:
    @staticmethod
    def _fn(ext):
        from app.core.collusion.analyzer import CollusionAnalyzer
        return CollusionAnalyzer._get_algorithm_d4_hits(ext)

    def test_ratio_hit(self):
        extractions = {
            1: {"price_info": {"total_price": "900000"}},
            2: {"price_info": {"total_price": "1000000"}},
        }
        hits = self._fn(extractions)
        assert (1, 2) in hits

    def test_tail_hit(self):
        extractions = {
            1: {"price_info": {"total_price": "100050"}},
            2: {"price_info": {"total_price": "300050"}},
        }
        hits = self._fn(extractions)
        assert (1, 2) in hits

    def test_no_hit_when_price_far_apart(self):
        extractions = {
            1: {"price_info": {"total_price": "100000"}},
            2: {"price_info": {"total_price": "500000"}},
        }
        hits = self._fn(extractions)
        assert (1, 2) not in hits

    def test_no_hit_when_missing_price(self):
        extractions = {
            1: {"price_info": {}},
            2: {"price_info": {"total_price": "500000"}},
        }
        hits = self._fn(extractions)
        assert hits == set()


# ---------------------------------------------------------------------------
# P1-1 get_smart_filter_pairs include_pricing_patterns
# ---------------------------------------------------------------------------

class TestSmartFilterPricingPatterns:
    @staticmethod
    def _build(extractions):
        from app.core.collusion.global_features import GlobalFeatureAnalyzer
        return GlobalFeatureAnalyzer(extractions, doc_names={d: f"文件{d}" for d in extractions})

    def test_pricing_pattern_pairs_added_when_flag_on(self):
        # 3 份文件报价等差 100/200/300 → _analyze_pricing 产出 equal_spacing
        ext = {
            1: {"price_info": {"total_price": "100"}},
            2: {"price_info": {"total_price": "200"}},
            3: {"price_info": {"total_price": "300"}},
        }
        gf = self._build(ext)
        gf.analyze()  # 触发缓存
        all_pairs = [(1, 2), (1, 3), (2, 3)]
        # candidate_scores 全 0 + 无 D1/D2 命中
        pairs_off = gf.get_smart_filter_pairs(all_pairs, {}, set(), include_pricing_patterns=False)
        pairs_on = gf.get_smart_filter_pairs(all_pairs, {}, set(), include_pricing_patterns=True)
        # 注意：当 must_llm 为空时 get_smart_filter_pairs 会 fallback 取 top-N，所以 off
        # 也可能有 pairs；这里只要求"开启后 ≥ 关闭后"
        assert set(pairs_on).issuperset(set(pairs_off) & set(all_pairs))
        # 开启后应包含由等差模式覆盖的所有对
        assert (1, 2) in pairs_on
        assert (2, 3) in pairs_on

    def test_outlier_pattern_adds_pairs_with_all_others(self):
        # 4 份文件，doc1 价格远低于其他 → outlier 类型
        ext = {
            1: {"price_info": {"total_price": "100000"}},  # outlier
            2: {"price_info": {"total_price": "500000"}},
            3: {"price_info": {"total_price": "510000"}},
            4: {"price_info": {"total_price": "520000"}},
        }
        gf = self._build(ext)
        gf.analyze()
        all_pairs = [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)]
        pairs_on = gf.get_smart_filter_pairs(all_pairs, {}, set(), include_pricing_patterns=True)
        # outlier(doc1) 应与 2/3/4 两两配对
        assert (1, 2) in pairs_on
        assert (1, 3) in pairs_on
        assert (1, 4) in pairs_on


# ---------------------------------------------------------------------------
# P1-2 _run_overall_synthesis 超时参数一致性（静态检查）
# ---------------------------------------------------------------------------

class TestOverallSynthesisTimeoutUnified:
    def test_analyzer_source_uses_synthesis_timeout_not_group(self):
        """P1-2: _run_overall_synthesis 主路径与重试路径都应使用
        get_collusion_synthesis_timeout，与 _run_synthesis 对齐。"""
        import re
        from pathlib import Path
        src = Path(__file__).parent.parent / "app" / "core" / "collusion" / "analyzer.py"
        text = src.read_text(encoding="utf-8")

        # 找到 _run_overall_synthesis 函数体
        match = re.search(
            r"async def _run_overall_synthesis\(.*?\n(?=    async def |\ndef )",
            text, re.DOTALL,
        )
        assert match, "未找到 _run_overall_synthesis 定义"
        body = match.group(0)

        assert "get_collusion_synthesis_timeout" in body, (
            "P1-2: _run_overall_synthesis 应使用 get_collusion_synthesis_timeout"
        )
        assert "get_collusion_group_timeout" not in body, (
            "P1-2: _run_overall_synthesis 不应再使用 get_collusion_group_timeout"
        )


# ---------------------------------------------------------------------------
# P1-3 build_industry_signal_context 扩充
# ---------------------------------------------------------------------------

class TestIndustrySignalContextExtended:
    def test_price_info_appears(self):
        from app.core.collusion.prompts import build_industry_signal_context
        ext = [
            {"price_info": {"total_price": "100万元"},
             "company_info": {"company_name": "公司A"}},
            {"price_info": {"total_price": "200万元"},
             "company_info": {"company_name": "公司B"}},
        ]
        out = build_industry_signal_context(tender_text=None, bid_extractions=ext)
        assert "price_info" in out
        assert "100万元" in out

    def test_subcontractors_appears(self):
        from app.core.collusion.prompts import build_industry_signal_context
        ext = [
            {"subcontractors": ["分包方X", "供应商Y"],
             "company_info": {"company_name": "公司A"}},
        ]
        out = build_industry_signal_context(tender_text=None, bid_extractions=ext)
        assert "分包方X" in out or "分包" in out

    def test_personnel_role_appears(self):
        from app.core.collusion.prompts import build_industry_signal_context
        ext = [
            {"personnel_list": [
                {"name": '<PERSON_A>', "role": "项目经理"},
                {"name": '<PERSON_B>', "role": "技术负责人"},
            ], "company_info": {"company_name": "公司A"}},
        ]
        out = build_industry_signal_context(tender_text=None, bid_extractions=ext)
        assert "项目经理" in out or "岗位" in out


# ---------------------------------------------------------------------------
# 有招标文件时以招标为主信源，并保留投标侧补充信号
# ---------------------------------------------------------------------------

class TestIndustrySignalContextTenderPrimary:
    def test_tender_present_uses_tender_only(self):
        from app.core.collusion.prompts import build_industry_signal_context
        out = build_industry_signal_context(
            tender_text="本项目为某市政道路工程施工招标，要求市政公用工程施工总承包资质。",
            bid_extractions=[
                {"company_info": {"company_name": "甲建设公司"},
                 "technical_highlights": ["独家专利工法XYZ"]},
            ],
        )
        assert "招标文件行业相关片段" in out
        assert "市政道路工程" in out
        # 关键：投标提取信号作为补充上下文保留，不再被招标前缀截断拖累。
        assert "投标侧补充信号" in out
        assert "甲建设公司" in out
        assert "独家专利工法XYZ" in out

    def test_no_tender_falls_back_to_bids(self):
        from app.core.collusion.prompts import build_industry_signal_context
        out = build_industry_signal_context(
            tender_text=None,
            bid_extractions=[{"company_info": {"company_name": "甲建设公司"}}],
        )
        assert "招标文件材料摘要" not in out
        assert "甲建设公司" in out

    def test_blank_tender_falls_back_to_bids(self):
        from app.core.collusion.prompts import build_industry_signal_context
        out = build_industry_signal_context(
            tender_text="   ",
            bid_extractions=[{"company_info": {"company_name": "甲建设公司"}}],
        )
        assert "甲建设公司" in out


# ---------------------------------------------------------------------------
# P1-4 _simple_synthesis 改造
# ---------------------------------------------------------------------------

class TestSimpleSynthesis:
    def test_no_bid_docs_param(self):
        """P1-4: bid_docs 参数应被删除或变为可选（签名检查）"""
        import inspect
        from app.core.collusion.analyzer import _simple_synthesis
        sig = inspect.signature(_simple_synthesis)
        params = list(sig.parameters.keys())
        # 不再是必填参数
        if "bid_docs" in params:
            assert sig.parameters["bid_docs"].default is not inspect.Parameter.empty, (
                "P1-4: bid_docs 若保留必须可选"
            )

    def test_high_risk_fallback_generates_suspected_groups(self):
        """P1-4: 高风险兜底应产出 top-3 suspected_groups 而非空列表"""
        from app.core.collusion.analyzer import _simple_synthesis
        pair_results = [
            {
                "overall_risk": "high",
                "pair_ref": {"doc_a_id": 1, "doc_b_id": 2, "doc_a_name": "A", "doc_b_name": "B"},
            },
            {
                "overall_risk": "medium",
                "pair_ref": {"doc_a_id": 3, "doc_b_id": 4, "doc_a_name": "C", "doc_b_name": "D"},
            },
            {
                "overall_risk": "low",
                "pair_ref": {"doc_a_id": 5, "doc_b_id": 6, "doc_a_name": "E", "doc_b_name": "F"},
            },
        ]
        # 调用签名灵活处理 bid_docs
        try:
            result = _simple_synthesis(pair_results)
        except TypeError:
            result = _simple_synthesis(pair_results, [])

        assert result["overall_risk"] == "high"
        groups = result.get("suspected_groups") or []
        assert len(groups) >= 1, "P1-4: 高风险时应生成至少一个兜底 suspected_group"
        # 检查第一个应是高风险
        assert groups[0].get("risk_level") == "high"

    def test_none_risk_empty_groups(self):
        from app.core.collusion.analyzer import _simple_synthesis
        pair_results = [
            {"overall_risk": "none", "pair_ref": {"doc_a_id": 1, "doc_b_id": 2}},
        ]
        try:
            result = _simple_synthesis(pair_results)
        except TypeError:
            result = _simple_synthesis(pair_results, [])

        assert result["overall_risk"] == "none"
        assert result.get("suspected_groups") == []
