"""串标修复 P0 阶段单测

覆盖 P0-1/P0-2/P0-3/P0-4 的核心行为：
- P0-1 `_format_group_evidence_for_llm`：优先消费 fused_evidence，回退 aggregated_evidence；
  _fused 占位符不污染输出
- P0-2 `_build_global_findings_context`：按优先级取 top-N，空集返回兜底文本
- P0-3 `/results` API 倒推映射 D4→pricing_pattern、D8→metadata_cluster 的字典兼容（不起 HTTP，只测转换函数）
- P0-4 `_finalize_pair_contract`：非法风险枚举 fallback 到 inconclusive + needs_review
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# P0-1 _format_group_evidence_for_llm
# ---------------------------------------------------------------------------

class TestFormatGroupEvidenceForLLM:
    @staticmethod
    def _fn(fused, aggregated, **kw):
        from app.core.collusion.analyzer import _format_group_evidence_for_llm
        return _format_group_evidence_for_llm(fused, aggregated, **kw)

    def test_fused_takes_priority_when_non_empty(self):
        fused = [
            {
                "dimension": "D1",
                "level": "first",
                "description": '<PERSON_A> 跨标出现',
                "involved_doc_names": ["文件A", "文件B", "文件C"],
            },
            {
                "dimension": "D2",
                "level": "first",
                "description": "法定代表人相同",
            },
        ]
        legacy = [{"dimension": "D7", "description": "legacy-only"}]
        out = self._fn(fused, legacy)
        assert '<PERSON_A> 跨标出现' in out
        assert "法定代表人相同" in out
        assert "legacy-only" not in out  # 不混入 legacy
        # level 优先级前缀存在
        assert "[first][D1]" in out

    def test_fused_empty_falls_back_to_aggregated(self):
        aggregated = [{"dimension": "D2", "description": "aggregated-backup"}]
        assert "aggregated-backup" in self._fn([], aggregated)
        assert "aggregated-backup" in self._fn(None, aggregated)

    def test_fused_placeholder_is_skipped(self):
        # 历史遗留：aggregated 中可能存在 {_fused: True} 占位符，格式化时应跳过
        aggregated = [
            {"_fused": True, "count": 3},
            {"dimension": "D1", "description": "真实证据"},
        ]
        out = self._fn(None, aggregated)
        assert "真实证据" in out
        assert "_fused" not in out
        assert "count" not in out

    def test_all_empty_returns_placeholder(self):
        assert "(无结构化证据)" in self._fn(None, None)
        assert "(无结构化证据)" in self._fn([], [])

    def test_max_items_cap(self):
        fused = [
            {
                "dimension": "D1",
                "level": "first",
                "description": f"finding-{i}",
            }
            for i in range(30)
        ]
        out = self._fn(fused, None, max_items=5)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 5

    def test_sorted_by_level_then_dimension(self):
        fused = [
            {"dimension": "D7", "level": "third", "description": "third-D7"},
            {"dimension": "D1", "level": "first", "description": "first-D1"},
            {"dimension": "D3", "level": "second", "description": "second-D3"},
        ]
        out = self._fn(fused, None)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert lines[0].startswith("- [first][D1]")
        assert lines[1].startswith("- [second][D3]")
        assert lines[2].startswith("- [third][D7]")


# ---------------------------------------------------------------------------
# P0-1 CollusionCluster 新字段
# ---------------------------------------------------------------------------

class TestCollusionClusterFusedField:
    def test_default_empty_list(self):
        from app.core.collusion.graph_cluster import CollusionCluster
        c = CollusionCluster(member_doc_ids=[1, 2], member_names=["A", "B"])
        assert c.fused_evidence == []

    def test_assignable(self):
        from app.core.collusion.graph_cluster import CollusionCluster
        c = CollusionCluster(member_doc_ids=[1, 2], member_names=["A", "B"])
        c.fused_evidence = [{"dimension": "D1", "description": "x"}]
        assert c.fused_evidence == [{"dimension": "D1", "description": "x"}]


# ---------------------------------------------------------------------------
# P0-1 灰度开关
# ---------------------------------------------------------------------------

class TestFeatureFlagFusedEvidence:
    def _reset(self, monkeypatch, enabled: bool, ratio: float):
        from app.core.config import settings
        monkeypatch.setattr(settings, "COLLUSION_FUSED_EVIDENCE_TO_LLM", enabled)
        monkeypatch.setattr(settings, "COLLUSION_FUSED_EVIDENCE_TO_LLM_RATIO", ratio)

    def test_disabled_default(self, monkeypatch):
        self._reset(monkeypatch, False, 1.0)
        from app.core.collusion.feature_flags import use_fused_evidence_to_llm
        assert use_fused_evidence_to_llm(42) is False

    def test_enabled_full_ratio(self, monkeypatch):
        self._reset(monkeypatch, True, 1.0)
        from app.core.collusion.feature_flags import use_fused_evidence_to_llm
        assert use_fused_evidence_to_llm(42) is True

    def test_enabled_zero_ratio_is_off(self, monkeypatch):
        self._reset(monkeypatch, True, 0.0)
        from app.core.collusion.feature_flags import use_fused_evidence_to_llm
        assert use_fused_evidence_to_llm(42) is False

    def test_none_project_id_disabled(self, monkeypatch):
        self._reset(monkeypatch, True, 0.5)
        from app.core.collusion.feature_flags import use_fused_evidence_to_llm
        assert use_fused_evidence_to_llm(None) is False

    def test_hash_stable(self, monkeypatch):
        """同一 project_id 多次判定结果应相同（可回放）"""
        self._reset(monkeypatch, True, 0.5)
        from app.core.collusion.feature_flags import use_fused_evidence_to_llm
        a = use_fused_evidence_to_llm(999)
        b = use_fused_evidence_to_llm(999)
        assert a == b


# ---------------------------------------------------------------------------
# P0-2 _build_global_findings_context
# ---------------------------------------------------------------------------

class TestBuildGlobalFindingsContext:
    @staticmethod
    def _fn(findings, **kw):
        from app.core.collusion.analyzer import _build_global_findings_context
        return _build_global_findings_context(findings, **kw)

    def test_empty_returns_placeholder(self):
        out = self._fn(None)
        assert "无跨文件全局发现" in out
        assert "" != out
        out2 = self._fn([])
        assert "无跨文件全局发现" in out2

    def test_priority_order_d1_d2_d4_d8(self):
        findings = [
            {"type": "metadata_cluster", "description": "D8-meta", "involved_names": []},
            {"type": "pricing_pattern", "description": "D4-price", "involved_names": []},
            {"type": "company_relation", "description": "D2-company", "involved_names": []},
            {"type": "personnel_overlap", "description": "D1-person", "involved_names": []},
        ]
        out = self._fn(findings)
        # D1 在最前面
        first_newline = out.find("\n")
        assert "D1-person" in out[: first_newline if first_newline >= 0 else len(out)]
        # D8 在最后
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert "D8-meta" in lines[-1]

    def test_labels_applied(self):
        out = self._fn([
            {"type": "personnel_overlap", "description": '<PERSON_A>'},
            {"type": "pricing_pattern", "description": "等差"},
        ])
        assert "D1 人员重叠" in out
        assert "D4 报价异常" in out

    def test_more_docs_first_within_same_type(self):
        findings = [
            {"type": "personnel_overlap", "description": "P1", "involved_doc_ids": [1, 2]},
            {"type": "personnel_overlap", "description": "P2", "involved_doc_ids": [1, 2, 3, 4]},
        ]
        out = self._fn(findings)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert "P2" in lines[0]  # 涉及文档更多的在前
        assert "P1" in lines[1]

    def test_max_items_cap_with_ellipsis(self):
        findings = [
            {"type": "personnel_overlap", "description": f"P{i}"}
            for i in range(15)
        ]
        out = self._fn(findings, max_items=5)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 6  # 5 条 + 1 条省略提示
        assert "已省略" in lines[-1]

    def test_involved_names_shown(self):
        out = self._fn([
            {"type": "personnel_overlap", "description": '<PERSON_A>',
             "involved_names": ["文件A", "文件B", "文件C"]}
        ])
        assert "涉及:" in out
        assert "文件A" in out

    def test_non_dict_items_skipped(self):
        findings = [
            "invalid-string",
            None,
            {"type": "personnel_overlap", "description": "valid"},
        ]
        out = self._fn(findings)
        assert "valid" in out
        assert "invalid-string" not in out


# ---------------------------------------------------------------------------
# P0-2 project_summary Prompt 占位符契约
# ---------------------------------------------------------------------------

class TestProjectSummaryPromptPlaceholder:
    def test_contains_global_findings_context_placeholder(self):
        from app.core.collusion.prompts import COLLUSION_PROJECT_SUMMARY_PROMPT
        assert "{global_findings_context}" in COLLUSION_PROJECT_SUMMARY_PROMPT
        # 其他已有占位符不应丢失
        assert "{pair_judgements}" in COLLUSION_PROJECT_SUMMARY_PROMPT
        assert "{groups_summary}" in COLLUSION_PROJECT_SUMMARY_PROMPT
        assert "{coverage_summary}" in COLLUSION_PROJECT_SUMMARY_PROMPT
        assert "{profile_quality_summary}" in COLLUSION_PROJECT_SUMMARY_PROMPT

    def test_format_with_all_placeholders_succeeds(self):
        """确保 .format 不会因占位符残缺抛 KeyError"""
        from app.core.collusion.prompts import COLLUSION_PROJECT_SUMMARY_PROMPT
        rendered = COLLUSION_PROJECT_SUMMARY_PROMPT.format(
            pair_judgements="[]",
            groups_summary="(无)",
            coverage_summary="{}",
            profile_quality_summary="{}",
            global_findings_context="（测试占位）",
        )
        assert "（测试占位）" in rendered


# ---------------------------------------------------------------------------
# P0-3 enrich_clusters_with_global_findings + /results 倒推
# ---------------------------------------------------------------------------

class _FakeGlobalFeatures:
    """轻量 mock GlobalFeatureResult，仅含 global_findings 字段"""
    def __init__(self, global_findings):
        self.global_findings = global_findings


class TestEnrichClustersWithGlobalFindings:
    @staticmethod
    def _cluster(member_doc_ids, risk="medium"):
        from app.core.collusion.graph_cluster import CollusionCluster
        return CollusionCluster(
            member_doc_ids=list(member_doc_ids),
            member_names=[f"文件{d}" for d in member_doc_ids],
            risk_level=risk,
            aggregated_evidence=[],
        )

    def test_d4_injected_into_covering_cluster(self):
        from app.core.collusion.graph_cluster import enrich_clusters_with_global_findings
        cluster = self._cluster([1, 2, 3])
        gf = _FakeGlobalFeatures([
            {
                "type": "pricing_pattern",
                "involved_doc_ids": [1, 2],
                "description": "报价等差",
            },
        ])
        extra = enrich_clusters_with_global_findings([cluster], gf, doc_names={1: "A", 2: "B", 3: "C"})
        assert extra == []  # 全部命中群组
        assert len(cluster.aggregated_evidence) == 1
        ev = cluster.aggregated_evidence[0]
        assert ev["dimension"] == "D4"
        assert ev["source"] == "global_features"
        assert ev["finding_type"] == "pricing_pattern"
        assert ev["description"] == "报价等差"

    def test_d8_injected_into_covering_cluster(self):
        from app.core.collusion.graph_cluster import enrich_clusters_with_global_findings
        cluster = self._cluster([10, 20])
        gf = _FakeGlobalFeatures([
            {
                "type": "metadata_cluster",
                "involved_doc_ids": [10, 20],
                "description": "PDF Creator 相同",
            },
        ])
        enrich_clusters_with_global_findings([cluster], gf, doc_names={})
        assert cluster.aggregated_evidence[0]["dimension"] == "D8"
        assert cluster.aggregated_evidence[0]["finding_type"] == "metadata_cluster"

    def test_d1_d2_injected_into_covering_cluster(self):
        """ 同源治理：D1/D2 全局发现现在也会注入 aggregated_evidence，
 以确保前端中栏 pair-level 能找到对应证据，消除"全局发现展示了 D2 关联但中栏无证据"的割裂感。"""
        from app.core.collusion.graph_cluster import enrich_clusters_with_global_findings
        cluster = self._cluster([1, 2])
        gf = _FakeGlobalFeatures([
            {"type": "personnel_overlap", "involved_doc_ids": [1, 2], "description": "人员 X"},
            {"type": "company_relation", "involved_doc_ids": [1, 2], "description": "企业 Y"},
        ])
        enrich_clusters_with_global_findings([cluster], gf, doc_names={})
        d1_items = [e for e in cluster.aggregated_evidence if e["dimension"] == "D1"]
        d2_items = [e for e in cluster.aggregated_evidence if e["dimension"] == "D2"]
        assert len(d1_items) == 1, "D1 人员重叠发现应注入覆盖群组的 aggregated_evidence"
        assert d1_items[0]["description"] == "人员 X"
        assert d1_items[0]["source"] == "global_features"
        assert len(d2_items) == 1, "D2 企业关联发现应注入覆盖群组的 aggregated_evidence"
        assert d2_items[0]["description"] == "企业 Y"
        assert d2_items[0]["source"] == "global_features"

    def test_unmatched_goes_to_extra(self):
        from app.core.collusion.graph_cluster import enrich_clusters_with_global_findings
        cluster = self._cluster([1, 2])
        gf = _FakeGlobalFeatures([
            {
                "type": "pricing_pattern",
                "involved_doc_ids": [100, 200],  # 不在任何群组
                "description": "游离报价异常",
            },
        ])
        extra = enrich_clusters_with_global_findings([cluster], gf, doc_names={100: "X", 200: "Y"})
        assert cluster.aggregated_evidence == []
        assert len(extra) == 1
        assert extra[0]["type"] == "pricing_pattern"
        assert extra[0]["involved_doc_ids"] == [100, 200]
        assert extra[0]["involved_names"] == ["X", "Y"]

    def test_partial_overlap_injects_into_matched_cluster(self):
        """finding 涉及 doc 与群组部分重叠时应注入（用于"主标方 + 外围共犯"场景）"""
        from app.core.collusion.graph_cluster import enrich_clusters_with_global_findings
        cluster = self._cluster([1, 2, 3])
        gf = _FakeGlobalFeatures([
            {
                "type": "pricing_pattern",
                "involved_doc_ids": [3, 9],  # 3 在群组、9 不在
                "description": "部分重叠报价",
            },
        ])
        extra = enrich_clusters_with_global_findings([cluster], gf, doc_names={})
        assert extra == []
        assert len(cluster.aggregated_evidence) == 1

    def test_empty_global_features_returns_empty(self):
        from app.core.collusion.graph_cluster import enrich_clusters_with_global_findings
        assert enrich_clusters_with_global_findings([], None) == []
        assert enrich_clusters_with_global_findings([], _FakeGlobalFeatures([])) == []

    def test_involved_doc_names_populated(self):
        from app.core.collusion.graph_cluster import enrich_clusters_with_global_findings
        cluster = self._cluster([1, 2])
        gf = _FakeGlobalFeatures([
            {"type": "pricing_pattern", "involved_doc_ids": [1, 2], "description": "x"},
        ])
        enrich_clusters_with_global_findings([cluster], gf, doc_names={1: "公司A", 2: "公司B"})
        names = cluster.aggregated_evidence[0]["involved_doc_names"]
        assert names == ["公司A", "公司B"]


class TestProjectSummaryPayloadWithGlobalFindingsExtra:
    def test_global_findings_extra_round_trip(self):
        """payload 构造应将 global_findings_extra 透传，CollusionProjectSummary 可校验通过"""
        from app.core.collusion.analyzer import _build_project_summary_payload
        from app.schemas.collusion import CollusionProjectSummary

        payload = _build_project_summary_payload(
            synthesis={"overall_risk": "low", "summary": "x"},
            pair_results=[],
            clusters=[],
            total_pairs=0,
            llm_pair_count=0,
            skipped_count=0,
            has_tender_doc=False,
            industry_profile=None,
            global_findings_extra=[{
                "type": "pricing_pattern",
                "involved_doc_ids": [1, 2],
                "involved_names": ["A", "B"],
                "description": "dx",
            }],
        )
        assert payload["global_findings_extra"][0]["type"] == "pricing_pattern"
        # Pydantic 校验通过
        model = CollusionProjectSummary.model_validate(payload)
        assert len(model.global_findings_extra) == 1

    def test_global_findings_extra_default_empty(self):
        from app.core.collusion.analyzer import _build_project_summary_payload
        payload = _build_project_summary_payload(
            synthesis={"overall_risk": "none", "summary": ""},
            pair_results=[],
            clusters=[],
            total_pairs=0,
            llm_pair_count=0,
            skipped_count=0,
            has_tender_doc=False,
            industry_profile=None,
        )
        assert payload["global_findings_extra"] == []


# ---------------------------------------------------------------------------
# P0-4 _finalize_pair_contract 异常枚举收敛
# ---------------------------------------------------------------------------

class TestFinalizePairContractIllegalRiskFallback:
    @staticmethod
    def _call(compare_result, analysis_mode="full"):
        from app.core.collusion.analyzer import _finalize_pair_contract
        from app.core.config import settings
        return _finalize_pair_contract(
            compare_result,
            analysis_mode=analysis_mode,
            similarity_score=0.6,
            high_threshold=settings.COLLUSION_SIMILARITY_HIGH,
            extraction_degraded=False,
        )

    def _positive_dim_payload(self, overall_risk: str):
        """构造一份"有维度且正分数"的 compare_result，确保命中 finalize 的末段逻辑"""
        return {
            "overall_risk": overall_risk,
            "d1_personnel": {
                "score": 0.9, "findings": [], "evidence_level": "first", "certainty": 0.8,
            },
        }

    def test_illegal_enum_becomes_inconclusive(self):
        """非法枚举应降级为 inconclusive + needs_review，不再静默落为 low: quality_flag 措辞从面向运维改为面向用户（"AI 对本对未返回有效风险等级"），
 同时新增 degradation_reason 结构化字段，便于 narrative_builder 拼装事实段。
 """
        result = self._call(self._positive_dim_payload("uncertain"))
        assert result["overall_risk"] == "inconclusive"
        assert result["risk_level"] is None
        assert result["decision_state"] == "needs_review"
        assert result["review_state"] == "needs_review"
        # 新文案以"未返回有效风险等级"为标识，且携带 raw 值方便用户判读
        flags = result.get("quality_flags") or []
        assert any("未返回有效风险等级" in f for f in flags)
        assert any("uncertain" in f for f in flags)
        # degradation_reason 结构化字段必须落库
        degradation = result.get("degradation_reason") or {}
        assert degradation.get("type") == "invalid_risk_enum"
        assert degradation.get("raw_value") == "uncertain"
        assert degradation.get("fallback") == "needs_review"

    def test_empty_string_is_illegal(self):
        result = self._call(self._positive_dim_payload(""))
        assert result["overall_risk"] == "inconclusive"
        assert result["decision_state"] == "needs_review"

    def test_random_string_is_illegal(self):
        result = self._call(self._positive_dim_payload("WARNING"))
        assert result["overall_risk"] == "inconclusive"

    def test_legal_high_pass_through(self):
        result = self._call(self._positive_dim_payload("high"))
        assert result["overall_risk"] == "high"
        assert result["risk_level"] == "high"
        assert result["decision_state"] == "decidable"

    def test_legal_medium_pass_through(self):
        result = self._call(self._positive_dim_payload("medium"))
        assert result["overall_risk"] == "medium"
        assert result["decision_state"] == "decidable"

    def test_legal_low_pass_through(self):
        result = self._call(self._positive_dim_payload("low"))
        assert result["overall_risk"] == "low"

    def test_illegal_enum_has_risk_explanation(self):
        result = self._call(self._positive_dim_payload("garbage"))
        assert "garbage" in result.get("risk_explanation", "")

    def test_inconclusive_is_legal_not_invalid_enum(self):
        """ 对齐：inconclusive 是合法'无法判定'语义，不得误判为非法枚举。

 历史缺陷：合法枚举校验集漏掉 inconclusive，导致 LLM 合法返回的"无法判定"
 被标成"AI 未返回有效风险等级（返回值='无法判定'）"，文案自相矛盾。
 """
        result = self._call(self._positive_dim_payload("inconclusive"))
        assert result["overall_risk"] == "inconclusive"
        assert result["risk_level"] is None
        assert result["decision_state"] == "needs_review"
        assert result["review_state"] == "needs_review"
        # degradation_reason 必须区分于 invalid_risk_enum
        degradation = result.get("degradation_reason") or {}
        assert degradation.get("type") == "inconclusive_insufficient_evidence"
        assert degradation.get("fallback") == "needs_review"
        # 关键：inconclusive 路径禁止出现"未返回有效风险等级"误导文案
        flags = result.get("quality_flags") or []
        assert not any("未返回有效风险等级" in f for f in flags)
        assert any("证据不足" in f for f in flags)

    # TODO(followup-backlog): 类似的"异常枚举 → 静默降为 low" 风险在
    # investigate 与 writer 流程的 _finalize_*_contract 里是否也存在？
    # 需要独立专项排查，不在本次修复范围。
