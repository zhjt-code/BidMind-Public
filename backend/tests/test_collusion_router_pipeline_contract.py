# This contract intentionally imports public symbols to assert compatibility.
# ruff: noqa: F401
"""Collusion Router 瘦身（R2-6）Phase 1 快照契约测试

锁定以下 6 类契约，作为后续把 17 个非 endpoint 业务函数迁移到
`collusion_view_service` / `collusion_status_service` / `collusion_summary_service`
三个 service 时的"行为锚点"：

| 类别 | 用例数 | 锁定内容 |
|---|---|---|
| A. 模块级出口契约 | 5 | router 顶层符号 + 6 endpoint + 17 helper + 4 module-level 常量/regex |
| B. Endpoint schema 字段 byte-equal | 6 | CollusionStatus / CollusionResults / 4 个嵌套 schema 字段集合 frozenset |
| C. retry payload byte-equal | 3 | _collusion_retry_payload 10 字段 + 8 retry node specs + thin forwarder 签名 |
| D. summary 收口规则锚点（防御） | 4 | clear_no_risk / partial_review_needed / suspicious_found / analysis_failed 决策路径 |
| E. sanitize 出口闸门规则锚点 | 3 | pair_id 替换 / 空括号清洗 / 嵌套递归清洗 |
| F. inspect.getsource 锚点契约 | 4 | _build_finding_items / _sanitize_collusion_results / status endpoint / results endpoint |

设计意图：
- 拆分前跑通这 25 用例作为 baseline
- 拆分过程中每个 Step 后跑同一组用例验证零退化
- 拆分后 router 内保留 thin forwarder 确保这些用例继续 pass
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace

import pytest

from app.api.routers import collusion as collusion_router
from app.api.routers.collusion import (
    CollusionRetryRequest,
    _build_evidences_from_legacy,
    _build_finding_items,
    _build_pair_ref_labels,
    _build_runtime_narrative_summary,
    _collusion_retry_payload,
    _derive_display_overall_risk,
    _derive_overall_risk,
    _derive_runtime_project_decision,
    _fractional_progress_from_heartbeat,
    _get_project_or_404,
    _group_fractional_progress_from_heartbeat,
    _heartbeat_message,
    _heartbeat_stage_pct,
    _highest_risk,
    _load_or_build_project_summary,
    _normalize_pair_ref,
    _normalize_suspected_groups,
    _recheck_project_terminal,
    _replace_collusion_pair_refs,
    _sanitize_collusion_results,
    _step_status,
    _strip_pdf_suffix,
    download_collusion_report,
    get_collusion_results,
    get_collusion_retry_state,
    get_collusion_status,
    retry_collusion,
    start_collusion_analyze,
)
from app.schemas.collusion import (
    AttentionItem,
    CollusionGroupResult,
    CollusionPairResult,
    CollusionProjectSummary,
    CollusionResults,
    CollusionStatus,
    CollusionStepStatus,
    CoverageStats,
    EvidenceRef,
    FindingItem,
    GlobalFindingItem,
    GroupRoleItem,
    SuspectedGroup,
    build_pair_id,
)

# ---------------------------------------------------------------------------
# A. 模块级出口契约（拆分后 router 必须保留这些符号供外部 import / patch）
# ---------------------------------------------------------------------------


class TestModuleLevelSymbols:
    """A 类: 模块级出口契约。"""

    def test_A1_router_top_level_endpoint_symbols(self):
        """router 顶层 6 个 endpoint 名 + CollusionRetryRequest 必须可 import。"""
        required = {
            "start_collusion_analyze",
            "get_collusion_retry_state",
            "retry_collusion",
            "get_collusion_status",
            "get_collusion_results",
            "download_collusion_report",
            "CollusionRetryRequest",
            "router",
        }
        missing = required - set(vars(collusion_router).keys())
        assert not missing, f"router 顶层缺失符号: {missing}"

    def test_A2_router_top_level_helper_thin_forwarders(self):
        """17 个 _xxx 业务函数必须可从 router 直接 import（拆分后通过 thin forwarder 兼容）。

        4 个测试文件直接 import 其中 6 个，剩余 11 个也必须保留供 inspect.getsource 锚点。
        """
        required_helpers = {
            # status / 进度计算域（6 个，async 含 _get_project_or_404 / _recheck_project_terminal）
            "_get_project_or_404",
            "_recheck_project_terminal",
            "_heartbeat_stage_pct",
            "_heartbeat_message",
            "_fractional_progress_from_heartbeat",
            "_group_fractional_progress_from_heartbeat",
            "_collusion_retry_payload",
            # view 拼装域（8 个）
            "_build_finding_items",
            "_sanitize_collusion_results",
            "_build_evidences_from_legacy",
            "_build_pair_ref_labels",
            "_replace_collusion_pair_refs",
            "_normalize_pair_ref",
            "_strip_pdf_suffix",
            "_step_status",
            # summary 收口域（7 个）
            "_derive_runtime_project_decision",
            "_build_runtime_narrative_summary",
            "_load_or_build_project_summary",
            "_normalize_suspected_groups",
            "_highest_risk",
            "_derive_overall_risk",
            "_derive_display_overall_risk",
        }
        missing = required_helpers - set(vars(collusion_router).keys())
        assert not missing, f"router 顶层 helper 缺失: {missing}"

    def test_A3_router_endpoint_signatures_unchanged(self):
        """6 个 endpoint 参数签名 byte-equal（保护前端契约）。"""
        # 所有 endpoint 都接受 project_id + current_user + db
        for endpoint in (
            start_collusion_analyze,
            get_collusion_retry_state,
            get_collusion_status,
            get_collusion_results,
            download_collusion_report,
        ):
            sig = inspect.signature(endpoint)
            params = list(sig.parameters.keys())
            assert "project_id" in params, f"{endpoint.__name__} 缺少 project_id 参数"
            assert "current_user" in params, f"{endpoint.__name__} 缺少 current_user 参数"
            assert "db" in params, f"{endpoint.__name__} 缺少 db 参数"

        # retry_collusion 多一个 body 参数
        retry_params = list(inspect.signature(retry_collusion).parameters.keys())
        assert {"project_id", "body", "current_user", "db"}.issubset(set(retry_params))

    def test_A4_collusion_retry_request_schema(self):
        """CollusionRetryRequest 字段集合 byte-equal。"""
        assert set(CollusionRetryRequest.model_fields.keys()) == {"node_key", "target_id"}

    def test_A5_router_prefix_unchanged(self):
        """APIRouter prefix 必须包含 /projects/{project_id}/collusion。"""
        assert collusion_router.router.prefix == "/projects/{project_id}/collusion"

    def test_A6_endpoint_status_aliases_all_resolvable(self):
        """ 防御：endpoint thin wrapper 中引用的所有 _status_* 别名必须可在 router 命名空间解析。

 历史教训：R2-7 把 results / download endpoint 改成 thin wrapper 调用
 `_status_build_collusion_results`，但漏掉在 import 列表里补上 `build_collusion_results`，
 导致请求一进入就 NameError → 500，前端拿到"暂无分析结果 Request failed with status code 500"。

 F4 仅检查源码字面量，未验证名字是否真的可解析；本测试通过反扫源码 + 命名空间查找
 覆盖该缺口，确保后续重构再次发生"thin wrapper 引用了未导入符号"会立即被本测试拦截。
 """
        endpoints = (
            start_collusion_analyze,
            get_collusion_retry_state,
            retry_collusion,
            get_collusion_status,
            get_collusion_results,
            download_collusion_report,
        )
        router_namespace = vars(collusion_router)
        unresolved: list[tuple[str, str]] = []
        for endpoint in endpoints:
            source = inspect.getsource(endpoint)
            for name in re.findall(r"\b_status_[A-Za-z_][A-Za-z0-9_]*", source):
                if name not in router_namespace:
                    unresolved.append((endpoint.__name__, name))
        assert not unresolved, (
            "router endpoint 引用了未在模块命名空间中导入的 _status_* 名字: "
            + ", ".join(f"{ep}->{n}" for ep, n in unresolved)
        )


# ---------------------------------------------------------------------------
# B. Endpoint schema 字段 byte-equal 契约（保护前端契约）
# ---------------------------------------------------------------------------


class TestEndpointSchemaFields:
    """B 类: schema 字段集合 byte-equal，前端依赖。"""

    def test_B1_collusion_status_field_set(self):
        """CollusionStatus 字段集合 frozenset byte-equal。"""
        expected = frozenset({
            "project_id", "overall_status", "overall_progress",
            "steps",
            "total_documents", "total_bid_docs", "total_pairs",
            "extracted_count", "compared_count",
            "total_llm_pairs", "skipped_pairs", "failed_pairs", "total_groups",
            "global_features_done", "cluster_done",
            "has_tender_doc", "error_message",
            "message", "is_recovering", "recovery_count",
            "retryable", "failed_node", "target_id", "failed_stage",
            "retry_action_label", "preserved_outputs", "reset_scope",
            "last_attempt", "retry_running", "last_error",
        })
        actual = frozenset(CollusionStatus.model_fields.keys())
        assert actual == expected, f"diff: {actual ^ expected}"

    def test_B2_collusion_results_field_set(self):
        """CollusionResults 字段集合 frozenset byte-equal。"""
        expected = frozenset({
            "project_id", "overall_risk",
            "project_decision", "project_risk_level", "decision_confidence", "review_required",
            "decision_basis", "decision_engine_version",
            "overall_risk_basis", "risk_distribution_basis",
            "risk_consistency_status", "risk_consistency_note",
            "has_tender_doc",
            "summary_source_version", "data_quality_flags", "profile_quality_flags",
            "coverage_stats",
            "industry_profile_snapshot", "industry_profile_confidence",
            "industry_profile_status", "industry_profile_signals",
            "factual_explanation",
            "factual_explanation_items",
            "similarity_matrix", "document_labels",
            "pair_results",
            "suspected_groups", "synthesis_summary", "narrative_summary", "investigation_suggestions",
            "groups", "global_findings", "extraction_warnings",
            "completed_at",
            # B1 失败合同（默认不返回，但字段存在）
            "failure_state", "failure_reason", "confidence", "gate_trace",
            "failure_review_required", "reviewed_at", "reviewed_by",
        })
        actual = frozenset(CollusionResults.model_fields.keys())
        assert actual == expected, f"diff: {actual ^ expected}"

    def test_B3_collusion_pair_result_field_set(self):
        """CollusionPairResult 字段集合 byte-equal。"""
        expected = frozenset({
            "pair_id", "doc_a_id", "doc_b_id", "doc_a_name", "doc_b_name",
            "overall_risk", "risk_level", "similarity_score",
            "d1_personnel_overlap", "d2_company_relation", "d3_text_similarity",
            "d4_price_pattern", "d5_qualification_overlap", "d6_format_similarity",
            "d7_abnormal_consistency", "d8_metadata_similarity",
            "findings", "evidence_chunks", "analysis_mode", "review_state", "analysis_state", "decision_state",
            "quality_flags", "consistency_flags", "risk_explanation",
            "attention_items", "dimension_certainties",
        })
        actual = frozenset(CollusionPairResult.model_fields.keys())
        assert actual == expected, f"diff: {actual ^ expected}"

    def test_B4_collusion_group_result_field_set(self):
        """CollusionGroupResult 字段集合 byte-equal。"""
        expected = frozenset({
            "group_index", "member_doc_ids", "member_names", "risk_level",
            "roles", "collusion_strategy", "key_evidence_summary", "investigation_focus",
            "related_pair_ids", "network_description", "findings",
            "group_confidence", "uncertain_edge_count",
        })
        actual = frozenset(CollusionGroupResult.model_fields.keys())
        assert actual == expected, f"diff: {actual ^ expected}"

    def test_B5_global_finding_item_field_set(self):
        """GlobalFindingItem 字段集合 byte-equal。"""
        expected = frozenset({
            "type", "involved_doc_ids", "involved_names", "description",
            "risk_pattern", "target_dim", "level", "certainty",
            "legal_reference", "matched_value", "left_field", "right_field",
        })
        actual = frozenset(GlobalFindingItem.model_fields.keys())
        assert actual == expected, f"diff: {actual ^ expected}"

    def test_B6_coverage_stats_field_set(self):
        """CoverageStats 字段集合 byte-equal（决策依据）。"""
        expected = frozenset({
            "total_pairs", "analyzed_pairs", "llm_analyzed_pairs",
            "algorithm_only_pairs", "skipped_pairs", "failed_pairs",
            "inconclusive_pairs", "review_required_pairs",
            "clear_pairs", "suspicious_pairs", "coverage_ratio",
        })
        actual = frozenset(CoverageStats.model_fields.keys())
        assert actual == expected, f"diff: {actual ^ expected}"


# ---------------------------------------------------------------------------
# C. retry payload byte-equal（与 R2-1 互补，锁定 router 内 thin forwarder）
# ---------------------------------------------------------------------------


class TestRetryPayloadByteEqual:
    """C 类: retry payload byte-equal 契约。"""

    def test_C1_collusion_retry_payload_output_keys(self):
        """_collusion_retry_payload 输出 10 字段集合 byte-equal（前端轮询契约）。"""
        payload = _collusion_retry_payload(project_id=42, error_message="boom")

        expected_keys = {
            "retryable", "failed_node", "target_id", "failed_stage",
            "retry_action_label", "preserved_outputs", "reset_scope",
            "last_attempt", "retry_running", "last_error",
        }
        assert set(payload.keys()) == expected_keys
        assert payload["retryable"] is True
        assert payload["failed_node"] == "collusion_analyze"
        assert payload["last_error"] == "boom"
        assert payload["retry_action_label"] == "重新发起串标分析"

    def test_C2_collusion_retry_payload_with_none_error(self):
        """_collusion_retry_payload last_error=None 保留为 None（兜底）。"""
        payload = _collusion_retry_payload(project_id=1)
        assert payload["last_error"] is None

    def test_C3_collusion_retry_payload_signature_byte_equal(self):
        """thin forwarder 签名 byte-equal（防拆分后参数名变化）。"""
        sig = inspect.signature(_collusion_retry_payload)
        params = list(sig.parameters.keys())
        assert params == ["project_id", "error_message"]
        assert sig.parameters["error_message"].default is None


# ---------------------------------------------------------------------------
# D. summary 收口规则锚点（防御）
# ---------------------------------------------------------------------------


class TestSummaryRulesAnchors:
    """D 类: summary 收口规则锚点。"""

    def _make_coverage(self, **overrides) -> CoverageStats:
        base = {
            "total_pairs": 2, "analyzed_pairs": 2, "llm_analyzed_pairs": 1,
            "algorithm_only_pairs": 0, "skipped_pairs": 0, "failed_pairs": 0,
            "inconclusive_pairs": 0, "review_required_pairs": 0,
            "clear_pairs": 0, "suspicious_pairs": 0, "coverage_ratio": 1.0,
        }
        base.update(overrides)
        return CoverageStats(**base)

    def test_D1_clear_no_risk_decision_path(self):
        """clear_no_risk 决策路径触发条件 byte-equal。

        所有 analyzed_pairs 都是 clear，且无 review_required / failed → clear_no_risk
        """
        coverage = self._make_coverage(
            clear_pairs=2, suspicious_pairs=0, review_required_pairs=0, failed_pairs=0,
        )
        decision = _derive_runtime_project_decision(
            pair_results=[], group_records=[], coverage_stats=coverage,
        )
        assert decision["project_decision"] == "clear_no_risk"
        assert decision["project_risk_level"] == "none"
        assert decision["decision_confidence"] == "high"
        assert decision["review_required"] is False

    def test_D2_suspicious_found_with_pair_evidence(self):
        """suspicious_found：pair 有可直接决策的异常证据 → suspicious_found。"""
        pair_with_risk = SimpleNamespace(
            overall_risk="high",
            analysis_mode="grouped_parallel",
            llm_analysis={"decision_state": "decidable", "risk_level": "high"},
        )
        coverage = self._make_coverage(
            suspicious_pairs=1, clear_pairs=0, failed_pairs=0, review_required_pairs=0,
        )
        decision = _derive_runtime_project_decision(
            pair_results=[pair_with_risk], group_records=[], coverage_stats=coverage,
        )
        assert decision["project_decision"] == "suspicious_found"
        assert decision["project_risk_level"] == "high"
        # 无 failed_pairs 且无 review_required_pairs → high confidence
        assert decision["decision_confidence"] == "high"
        assert decision["review_required"] is False

    def test_D3_partial_review_needed_when_mix_of_clear_and_pending(self):
        """partial_review_needed：clear + skipped 混合时触发，narrative 含'暂不直接给出'。

 防御：narrative 必须由规则裁决器实时生成，不沿用 stored LLM 文案。
 """
        project = SimpleNamespace(collusion_summary={
            "overall_risk": "none", "summary": "旧版快照",
        })
        pair_clear = SimpleNamespace(
            overall_risk="none",
            analysis_mode="algorithm_only",
            llm_analysis={"decision_state": "decidable", "risk_level": "none"},
        )
        pair_skip = SimpleNamespace(
            overall_risk="inconclusive",
            analysis_mode="skipped",
            llm_analysis={"decision_state": "skipped", "risk_level": None},
        )
        coverage = self._make_coverage(
            total_pairs=2, analyzed_pairs=2,
            skipped_pairs=1, clear_pairs=1, review_required_pairs=1,
        )

        summary = _load_or_build_project_summary(
            project=project,
            pair_results=[pair_clear, pair_skip],
            group_records=[],
            coverage_stats=coverage,
            has_tender_doc=True,
            doc_id_to_name={},
        )
        assert summary.project_decision == "partial_review_needed"
        assert summary.review_required is True

    def test_D4_analysis_failed_when_all_pairs_failed(self):
        """analysis_failed：所有 analyzed_pairs 都失败 → analysis_failed + review_required=True。"""
        coverage = self._make_coverage(
            total_pairs=2, analyzed_pairs=2, failed_pairs=2, clear_pairs=0,
        )
        decision = _derive_runtime_project_decision(
            pair_results=[], group_records=[], coverage_stats=coverage,
        )
        assert decision["project_decision"] == "analysis_failed"
        assert decision["project_risk_level"] is None
        assert decision["decision_confidence"] == "low"
        assert decision["review_required"] is True


# ---------------------------------------------------------------------------
# E. sanitize 出口闸门规则锚点
# ---------------------------------------------------------------------------


class TestSanitizeRulesAnchors:
    """E 类: sanitize 出口闸门契约（_sanitize_collusion_results）。"""

    def test_E1_pair_id_replaced_with_label(self):
        """pair_id=xxx:yyy 与裸 xxx:yyy 都被替换为 甲公司×乙公司 形态。"""
        results = CollusionResults(
            project_id=1,
            overall_risk="none",
            project_decision="clear_no_risk",
            project_risk_level="none",
            decision_confidence="high",
            review_required=False,
            pair_results=[CollusionPairResult(
                pair_id="100:200", doc_a_id=100, doc_b_id=200,
                doc_a_name="甲公司.pdf", doc_b_name="乙公司.pdf",
                overall_risk="none",
            )],
            synthesis_summary="项目对 pair_id=100:200 进行了分析，裸引用 100:200 应展示名称。",
        )

        _sanitize_collusion_results(results)

        assert "pair_id" not in results.synthesis_summary
        assert "100:200" not in results.synthesis_summary
        assert "甲公司×乙公司" in results.synthesis_summary

    def test_E2_empty_parens_stripped(self):
        """未知 pair_id 替换后产生的空括号必须被清洗。"""
        results = CollusionResults(
            project_id=1,
            overall_risk="none",
            project_decision="clear_no_risk",
            project_risk_level="none",
            decision_confidence="high",
            review_required=False,
            pair_results=[],
            investigation_suggestions=[
                "未知文件对（pair_id=999:1000）不应留下空括号。",
            ],
        )

        _sanitize_collusion_results(results)

        assert results.investigation_suggestions == ["未知文件对不应留下空括号。"]
        assert "（）" not in (results.investigation_suggestions[0] or "")
        assert "()" not in (results.investigation_suggestions[0] or "")

    def test_E3_nested_attention_items_findings_evidences_sanitized(self):
        """嵌套结构 attention_items / findings.evidences 必须递归清洗。"""
        results = CollusionResults(
            project_id=1,
            overall_risk="none",
            project_decision="clear_no_risk",
            project_risk_level="none",
            decision_confidence="high",
            review_required=False,
            pair_results=[CollusionPairResult(
                pair_id="1:2", doc_a_id=1, doc_b_id=2,
                doc_a_name="A.pdf", doc_b_name="B.pdf",
                overall_risk="none",
                findings=[FindingItem(
                    pair_id="1:2", dimension="D1", level="first",
                    description="人员重叠 pair_id=1:2",
                    detail="详情含 pair_id=1:2",
                    evidences=[EvidenceRef(doc_id=1, doc_name="A.pdf", quote="quote pair_id=1:2")],
                )],
                attention_items=[AttentionItem(
                    category="business_relationship",
                    dimension="D1",
                    summary="关注 pair_id=1:2 业务往来",
                    detail="详情 pair_id=1:2",
                    source_reason="原因 pair_id=1:2",
                )],
            )],
        )

        _sanitize_collusion_results(results)

        finding = results.pair_results[0].findings[0]
        assert "pair_id" not in (finding.description or "")
        assert "pair_id" not in (finding.detail or "")
        for ev in finding.evidences:
            assert "pair_id" not in (ev.quote or "")

        ai = results.pair_results[0].attention_items[0]
        assert "pair_id" not in (ai.summary or "")
        assert "pair_id" not in (ai.detail or "")
        assert "pair_id" not in (ai.source_reason or "")


# ---------------------------------------------------------------------------
# F. inspect.getsource 锚点契约（防止拆分后业务规则在源码中丢失）
# ---------------------------------------------------------------------------


class TestInspectGetsourceAnchors:
    """F 类: inspect.getsource 锚点契约。

    通过断言 router 内函数源码包含关键字面量，防止：
    - 拆分时业务规则被遗漏迁移
    - thin forwarder 失去关键调用链
    - 源码字面量被 R2-3 同款误删除
    """

    def test_F1_build_finding_items_has_structured_dispatch(self):
        """_build_finding_items 源码必须含 'has_structured' 字面量（业务分发路径锚点）。"""
        source = inspect.getsource(_build_finding_items)
        assert "has_structured" in source, "失去结构化分发逻辑会导致 finding 拼装回归"

    def test_F2_sanitize_collusion_results_calls_sanitize_user_facing_text(self):
        """_sanitize_collusion_results 源码必须含 sanitize_user_facing_text 调用。

 同源问题：narrative_summary / synthesis_summary 等字段历史上从
 LLM 直通到 UI，导致 inconclusive / unknown 等英文枚举裸出。
 """
        source = inspect.getsource(_sanitize_collusion_results)
        assert "sanitize_user_facing_text" in source, "失去 sanitize_user_facing_text 调用会导致 LLM 文案裸出"
        assert "_build_pair_ref_labels" in source or "pair_labels" in source

    def test_F3_compute_collusion_status_calls_recovery_and_recheck(self):
        """compute_collusion_status 源码必须含心跳超时 + 自动恢复 + 终态二次检查链路。

 防止拆分时丢失 同源的"恢复重投前终态二次确认"防御链路。

 Step 6 后：endpoint 已变为 thin wrapper（调用 compute_collusion_status），
 实际编排链路位于 collusion_status_service.compute_collusion_status，
 故锚点检查移至 service 层。
 """
        from app.services.collusion_status_service import compute_collusion_status

        source = inspect.getsource(compute_collusion_status)
        assert "recheck_project_terminal" in source, "失去终态二次检查会导致 worker 收尾与轮询竞态重投"
        assert "increment_recovery_count" in source, "失去恢复计数会导致无限重投"
        assert "HEARTBEAT_TIMEOUT" in source or "heartbeat" in source.lower()

        # 同时验证 endpoint 仍然是 thin wrapper（调用 compute_collusion_status）
        endpoint_source = inspect.getsource(get_collusion_status)
        assert "compute_collusion_status" in endpoint_source, (
            "endpoint 必须 thin wrapper 到 compute_collusion_status，"
            "否则状态计算逻辑会在 router 内重新堆积"
        )

    def test_F4_build_collusion_results_calls_summary_and_sanitize_in_order(self):
        """build_collusion_results 源码必须含 summary_load_or_build_project_summary 和 view_sanitize_collusion_results 调用顺序。

 防止拆分时丢失"summary 收口 → sanitize 出口闸门"的关键调用顺序（+）。

 Step 7 后：endpoint 已变为 thin wrapper（调用 build_collusion_results），
 实际拼装链路位于 collusion_status_service.build_collusion_results，
 故锚点检查移至 service 层。
 """
        from app.services.collusion_status_service import build_collusion_results

        source = inspect.getsource(build_collusion_results)
        idx_summary = source.find("summary_load_or_build_project_summary")
        idx_sanitize = source.find("view_sanitize_collusion_results")
        assert idx_summary >= 0, "失去 summary 单真源调用会绕过 "
        assert idx_sanitize >= 0, "失去 sanitize 出口闸门调用会导致 LLM 文案裸出"
        assert idx_summary < idx_sanitize, "summary 收口必须在 sanitize 出口闸门之前"

        # 同时验证 endpoint 与 download 都改为 thin wrapper（调用 service）
        endpoint_source = inspect.getsource(get_collusion_results)
        assert "_status_build_collusion_results" in endpoint_source, (
            "endpoint 必须 thin wrapper 到 build_collusion_results"
        )
        download_source = inspect.getsource(download_collusion_report)
        assert "_status_build_collusion_results" in download_source, (
            "download endpoint 必须复用 build_collusion_results service 层（不再调 endpoint）"
        )


# ---------------------------------------------------------------------------
# 额外：纯函数级 byte-equal 锚点（小工具函数 R2-6 拆分时易被遗漏）
# ---------------------------------------------------------------------------


class TestPureUtilityHelpers:
    """G 类: 纯工具函数 byte-equal 锚点（每个函数 1 用例，作为冒烟级回归）。"""

    def test_G1_strip_pdf_suffix(self):
        assert _strip_pdf_suffix("甲公司.pdf") == "甲公司"
        assert _strip_pdf_suffix("甲公司.PDF") == "甲公司"
        assert _strip_pdf_suffix("甲公司") == "甲公司"

    def test_G2_normalize_pair_ref(self):
        assert _normalize_pair_ref("12:34") == "12:34"
        assert _normalize_pair_ref("34:12") == "12:34"
        assert _normalize_pair_ref("invalid") == "invalid"

    def test_G3_highest_risk(self):
        assert _highest_risk(["low", "high", "medium"]) == "high"
        assert _highest_risk(["none", "low"]) == "low"
        assert _highest_risk([]) == "inconclusive"

    def test_G4_step_status_thresholds(self):
        assert _step_status(100, "running") == "completed"
        assert _step_status(50, "running") == "running"
        assert _step_status(0, "running") == "waiting"
        assert _step_status(0, "idle") == "waiting"

    def test_G5_heartbeat_stage_pct_clamps(self):
        assert _heartbeat_stage_pct({"stage_progress_pct": 50}) == 50
        assert _heartbeat_stage_pct({"stage_progress_pct": 150}) == 100
        assert _heartbeat_stage_pct({"stage_progress_pct": -10}) == 0
        assert _heartbeat_stage_pct(None) == 0
        assert _heartbeat_stage_pct({"stage_progress_pct": "bad"}) == 0

    def test_G6_fractional_progress_short_circuit(self):
        # total <= 0 → 0
        assert _fractional_progress_from_heartbeat(0, 0, None) == 0
        # completed >= total → 100
        assert _fractional_progress_from_heartbeat(5, 5, None) == 100
        # 心跳进度提升
        result = _fractional_progress_from_heartbeat(
            completed_count=2, total_count=5, heartbeat={"stage_progress_pct": 55}
        )
        assert result == 51  # 与 test_progress_standardization 同款契约

    def test_G7_build_pair_ref_labels(self):
        results = CollusionResults(
            project_id=1, overall_risk="none",
            project_decision="clear_no_risk", project_risk_level="none",
            decision_confidence="high", review_required=False,
            pair_results=[CollusionPairResult(
                pair_id="3:5", doc_a_id=3, doc_b_id=5,
                doc_a_name="甲.pdf", doc_b_name="乙.pdf",
                overall_risk="none",
            )],
        )
        labels = _build_pair_ref_labels(results)
        assert labels == {"3:5": "甲×乙"}

    def test_G8_derive_overall_risk_takes_max_of_pair_and_group(self):
        pair_results = [SimpleNamespace(overall_risk="medium")]
        group_records = [SimpleNamespace(risk_level="high")]
        assert _derive_overall_risk(pair_results, group_records) == "high"
        assert _derive_overall_risk(pair_results, []) == "medium"
        assert _derive_overall_risk([], []) == "inconclusive"

    def test_G9_derive_display_overall_risk_prefers_groups(self):
        pair_results = [SimpleNamespace(overall_risk="low")]
        group_records = [SimpleNamespace(risk_level="high")]
        # 有群组按群组
        assert _derive_display_overall_risk(pair_results, group_records) == ("high", "groups")
        # 无群组按 pair
        assert _derive_display_overall_risk(pair_results, []) == ("low", "pairs")
        # 都无 → inconclusive
        assert _derive_display_overall_risk([], []) == ("inconclusive", "pairs")
