from types import SimpleNamespace

from app.api.routers.collusion import _load_or_build_project_summary, _sanitize_collusion_results
from app.core.collusion.analyzer import (
    _apply_profile_confidence_to_candidate_scores,
    _build_failed_pair_result,
    _build_profile_quality_flags,
    _build_project_summary_payload,
    _inject_d8_from_metadata,
)
from app.core.collusion.graph_cluster import build_collusion_graph
from app.core.collusion.prompts import build_industry_signal_context
from app.schemas.collusion import CollusionPairResult, CollusionResults, CoverageStats


def test_build_failed_pair_result_returns_inconclusive_placeholder() -> None:
    doc_a = SimpleNamespace(id=8, filename="乙公司.pdf")
    doc_b = SimpleNamespace(id=3, filename="甲公司.pdf")

    result = _build_failed_pair_result(
        doc_a=doc_a,
        doc_b=doc_b,
        similarity_score=0.81,
        reason="LLM timeout",
    )

    assert result["overall_risk"] == "inconclusive"
    assert result["review_state"] == "failed"
    assert result["analysis_state"] == "failed"
    assert result["decision_state"] == "failed"
    assert result["risk_level"] is None
    assert result["pair_id"] == "3:8"
    assert result["pair_ref"]["doc_a_name"] == "甲公司.pdf"
    assert result["pair_ref"]["doc_b_name"] == "乙公司.pdf"


def test_build_project_summary_payload_includes_coverage_and_quality_flags() -> None:
    pair_results = [
        {
            "overall_risk": "high",
            "risk_level": "high",
            "analysis_mode": "grouped_parallel",
            "review_state": "completed",
            "analysis_state": "completed",
            "decision_state": "decidable",
        },
        {
            "overall_risk": "inconclusive",
            "analysis_mode": "failed",
            "review_state": "failed",
            "analysis_state": "failed",
            "decision_state": "failed",
        },
        {
            "overall_risk": "none",
            "risk_level": "none",
            "analysis_mode": "algorithm_only",
            "review_state": "completed",
            "analysis_state": "completed",
            "decision_state": "decidable",
        },
    ]
    synthesis = {
        "overall_risk": "high",
        "summary": "发现高风险串通信号",
        "suspected_groups": [],
        "investigation_suggestions": ["核查关键人员身份"],
    }

    payload = _build_project_summary_payload(
        synthesis=synthesis,
        pair_results=pair_results,
        clusters=[],
        total_pairs=5,
        llm_pair_count=4,
        skipped_count=2,
        has_tender_doc=False,
        industry_profile={
            "industry_type": "unknown",
            "confidence": "low",
            "signals": [{"signal_type": "term", "value": "油田", "source": "tender", "source_refs": ["第2页"], "confidence": 0.7}],
        },
    )

    coverage = payload["coverage_stats"]
    assert coverage["total_pairs"] == 5
    assert coverage["analyzed_pairs"] == 5
    assert coverage["skipped_pairs"] == 2
    assert coverage["failed_pairs"] == 1
    assert coverage["algorithm_only_pairs"] == 1
    assert coverage["review_required_pairs"] == 2
    assert coverage["clear_pairs"] == 1
    assert coverage["suspicious_pairs"] == 1
    assert coverage["coverage_ratio"] == 1.0
    # 覆盖率/降级类事实信号已迁移到 factual_explanation 事实段，
    # data_quality_flags 仅保留"招标参照缺失"这类需要立刻引起用户决策的告警。
    assert any("未上传招标文件参照" in flag for flag in payload["data_quality_flags"])
    assert not any("未进入深度分析" in flag for flag in payload["data_quality_flags"])
    assert not any("分析失败" in flag for flag in payload["data_quality_flags"])
    assert payload["industry_profile_confidence"] == "low"
    assert payload["industry_profile_snapshot"]["industry_type"] == "unknown"
    # industry_profile_status 字段必须落库（旧 snapshot 无 status 时为 None）
    assert "industry_profile_status" in payload
    assert any("行业画像" in flag for flag in payload["profile_quality_flags"])
    # factual_explanation 字段必须存在于 payload 顶层（即便 synthesis 未注入也保持键位）
    assert "factual_explanation" in payload
    assert payload["project_decision"] == "suspicious_found"
    assert payload["project_risk_level"] == "high"
    assert payload["decision_confidence"] == "medium"
    assert payload["review_required"] is True
    assert payload["decision_engine_version"] >= 2
    assert payload["summary"] == "发现高风险串通信号"
    assert payload["narrative_summary"] != payload["summary"]
    assert payload["risk_distribution_basis"] == "pairs"


def test_build_project_summary_payload_returns_partial_review_needed_when_only_clear_and_pending() -> None:
    payload = _build_project_summary_payload(
        synthesis={
            "overall_risk": "none",
            "summary": "LLM 认为未发现异常",
            "suspected_groups": [],
            "investigation_suggestions": [],
        },
        pair_results=[
            {
                "overall_risk": "none",
                "risk_level": "none",
                "analysis_mode": "grouped_parallel",
                "review_state": "completed",
                "analysis_state": "completed",
                "decision_state": "decidable",
            },
            {
                "overall_risk": "inconclusive",
                "analysis_mode": "skipped",
                "review_state": "unreviewed",
                "analysis_state": "skipped",
                "decision_state": "skipped",
            },
        ],
        clusters=[],
        total_pairs=2,
        llm_pair_count=1,
        skipped_count=1,
        has_tender_doc=True,
        industry_profile={"industry_type": "市政", "confidence": "high", "signals": [{"signal_type": "term"}]},
    )

    assert payload["project_decision"] == "partial_review_needed"
    assert payload["project_risk_level"] == "none"
    assert payload["review_required"] is True
    assert payload["summary"] == "LLM 认为未发现异常"
    assert payload["narrative_summary"] != payload["summary"]
    # partial_review_needed 场景下 narrative_summary 应给出事实性统计文案（而非主观结论评价）
    # 涵盖"已完成 X 对比对"与"待复核"两个语义关键点，锁定统计口径正确
    assert "待复核" in payload["narrative_summary"], (
        f"partial_review_needed 时 narrative_summary 应包含‘待复核’，实际：{payload['narrative_summary']}"
    )
    assert "已完成" in payload["narrative_summary"], (
        f"partial_review_needed 时 narrative_summary 应包含对比完成情况，实际：{payload['narrative_summary']}"
    )


def test_build_industry_signal_context_uses_uploaded_materials_only() -> None:
    """: 有招标文件时以招标为主信源，同时保留投标补充信号。

 旧轻量版把"招标优先"实现成"招标独占"，导致招标前缀缺少行业
 信息时投标侧强信号也被丢弃。
 """
    context = build_industry_signal_context(
        tender_text="石油库区改造工程，包含输油管线、防爆电气、油气回收系统。",
        bid_extractions=[
            {
                "technical_highlights": ["输油管线不停输切改方案", "防爆电气安装"],
                "qualifications": ["石油化工工程施工总承包二级"],
                "past_projects": ["某油库工艺改造工程"],
                "company_info": {"company_name": "甲公司"},
            }
        ],
    )

    assert "石油库区改造工程" in context
    assert "招标文件行业相关片段" in context
    # 招标优先但不独占：投标侧结构化信号作为补充上下文保留。
    assert "投标侧补充信号" in context
    assert "输油管线不停输切改方案" in context
    assert "甲公司" in context
    assert "不得猜测行业" not in context


def test_build_industry_signal_context_bids_only_when_no_tender() -> None:
    """无招标文件时回退到投标信号（原逻辑），投标侧信号应正常出现。"""
    context = build_industry_signal_context(
        tender_text=None,
        bid_extractions=[
            {
                "technical_highlights": ["输油管线不停输切改方案"],
                "qualifications": ["石油化工工程施工总承包二级"],
                "company_info": {"company_name": "甲公司"},
            }
        ],
    )

    assert "输油管线不停输切改方案" in context
    assert "石油化工工程施工总承包二级" in context


def test_apply_profile_confidence_to_candidate_scores_degrades_low_confidence() -> None:
    scores = {(1, 2): 0.8}

    high = _apply_profile_confidence_to_candidate_scores(scores, industry_profile={"confidence": "high"})
    low = _apply_profile_confidence_to_candidate_scores(scores, industry_profile={"confidence": "low"})

    assert high[(1, 2)] == 0.8
    assert low[(1, 2)] < high[(1, 2)]


def test_build_profile_quality_flags_marks_unknown_profile() -> None:
    """: 旧 snapshot（无 status 字段）按 signals/confidence 推断为
 insufficient_signals，输出包含"信号不足"短语的告警。
 """
    flags = _build_profile_quality_flags({
        "industry_type": "unknown",
        "confidence": "low",
        "signals": [],
    })

    assert any("信号不足" in flag for flag in flags)


def test_graph_cluster_keeps_uncertain_high_similarity_edges() -> None:
    pair_results = [
        {
            "pair_ref": {"doc_a_id": 1, "doc_b_id": 2},
            "overall_risk": "inconclusive",
            "review_state": "failed",
            "similarity_score": 0.82,
            "key_findings": [],
        }
    ]
    global_features = SimpleNamespace(
        personnel_overlaps={},
        company_edges=[],
        pricing_patterns=[],
        metadata_clusters=[],
    )

    clusters = build_collusion_graph(pair_results, global_features, doc_names={1: "A", 2: "B"})

    assert len(clusters) == 1
    assert clusters[0].uncertain_edge_count == 1
    assert clusters[0].group_confidence > 0


def test_load_or_build_project_summary_recomputes_legacy_snapshot() -> None:
    project = SimpleNamespace(collusion_summary={
        "overall_risk": "none",
        "summary": "旧版快照：未发现异常",
    })
    pair_results = [
        SimpleNamespace(
            overall_risk="none",
            analysis_mode="algorithm_only",
            llm_analysis={"decision_state": "decidable", "risk_level": "none"},
        ),
        SimpleNamespace(
            overall_risk="inconclusive",
            analysis_mode="skipped",
            llm_analysis={"decision_state": "skipped", "risk_level": None},
        ),
    ]

    summary = _load_or_build_project_summary(
        project=project,
        pair_results=pair_results,
        group_records=[],
        coverage_stats=CoverageStats(
            total_pairs=2,
            analyzed_pairs=2,
            skipped_pairs=1,
            review_required_pairs=1,
            clear_pairs=1,
        ),
        has_tender_doc=True,
        doc_id_to_name={},
    )

    assert summary.project_decision == "partial_review_needed"
    assert summary.review_required is True
    # load_or_build_project_summary 应把 LLM 文案存入 summary.summary，
    # 把规则实时生成的叙述存入 summary.narrative_summary（不沿用旧 LLM 文案）
    assert summary.summary == "旧版快照：未发现异常", "旧版快照的 LLM 文案应原样保留在 summary 字段"
    assert "待复核" in summary.narrative_summary, (
        f"partial_review_needed 时 narrative_summary 应包含‘待复核’，实际：{summary.narrative_summary}"
    )


def test_sanitize_collusion_results_resolves_pair_id_before_stripping() -> None:
    results = CollusionResults(
        project_id=1,
        overall_risk="none",
        project_decision="clear_no_risk",
        project_risk_level="none",
        decision_confidence="high",
        review_required=False,
        pair_results=[
            CollusionPairResult(
                pair_id="597:598",
                doc_a_id=597,
                doc_b_id=598,
                doc_a_name="甲公司.pdf",
                doc_b_name="乙公司.pdf",
                overall_risk="none",
            )
        ],
        synthesis_summary=(
            "项目仅包含一个文件对（pair_id=597:598），经分析未发现串通投标证据。"
            "裸引用 597:598 应展示名称。"
        ),
        narrative_summary="项目级综合结论为clear_no_risk。",
        investigation_suggestions=[
            "复核 pair_id=598:597 的原文证据。",
            "未知文件对（pair_id=999:1000）不应留下空括号。",
        ],
    )

    _sanitize_collusion_results(results)

    assert "" not in results.synthesis_summary
    assert "pair_id" not in results.synthesis_summary
    assert "597:598" not in results.synthesis_summary
    assert "甲公司×乙公司" in results.synthesis_summary
    assert results.synthesis_summary.count("甲公司×乙公司") == 2
    assert "未发现串标嫌疑" in results.narrative_summary
    assert results.investigation_suggestions == [
        "复核 甲公司×乙公司 的原文证据。",
        "未知文件对不应留下空括号。",
    ]


def test_sanitize_resolves_file_pair_doc_ids_to_uploaded_filenames() -> None:
    results = CollusionResults(
        project_id=1,
        overall_risk="medium",
        project_decision="suspicious_found",
        project_risk_level="medium",
        decision_confidence="medium",
        review_required=False,
        pair_results=[
            CollusionPairResult(
                pair_id="2517:2518",
                doc_a_id=2517,
                doc_b_id=2518,
                doc_a_name="甲公司投标文件.pdf",
                doc_b_name="乙公司技术方案.pdf",
                overall_risk="medium",
            )
        ],
        synthesis_summary="针对文件对2517与2518的分析显示，元数据时间戳接近。",
    )

    _sanitize_collusion_results(results)

    assert "引用无效" not in results.synthesis_summary
    assert "2517" not in results.synthesis_summary
    assert "2518" not in results.synthesis_summary
    assert "甲公司投标文件" in results.synthesis_summary
    assert "乙公司技术方案" in results.synthesis_summary


def test_inject_d8_from_metadata_consumes_parser_metadata_keys() -> None:
    compare_result: dict = {}
    info_a = {
        "metadata_info": {
            "creator_tool": "Writer",
            "author": "HX47_Test_Prep",
            "producer": "LibreOffice",
            "keywords": "HX-47, synthetic, collusion-test",
            "subject": "Bid Collusion Detection Synthetic Sample",
            "creation_date": "D:20260409014734Z'",
            "mod_date": "D:20260409101237+08'00'",
        }
    }
    info_b = {
        "metadata_info": {
            "creator_tool": "Writer",
            "author": "HX47_Test_Prep",
            "producer": "LibreOffice",
            "keywords": "HX-47, synthetic, collusion-test",
            "subject": "Bid Collusion Detection Synthetic Sample",
            "creation_date": "D:20260409014738Z'",
            "mod_date": "D:20260409101030+08'00'",
        }
    }

    _inject_d8_from_metadata(compare_result, info_a, info_b)

    d8 = compare_result["d8_metadata"]
    assert d8["score"] > 0.0
    assert any("PDF生成工具" in finding["summary"] for finding in d8["findings"])
    assert any("创建时间接近" in finding["summary"] for finding in d8["findings"])
