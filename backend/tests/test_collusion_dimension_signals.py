"""T1 治理护栏测试：维度信号项保证"有实质内容的得分维度可展示"。

映射真实缺陷：维度"评分通道"与"证据展示通道"门槛不一致，导致矩阵/雷达
有得分但中栏只剩 D3、其余得分维度被静默丢弃（详见 docs/bug_tracking.md）。

覆盖：
- pair 级合成（有实质内容才出信号；空壳不合成，）
- 一致性护栏（有实质/正式证据的得分维度，extract+synthesize 后必被覆盖）
- group 级融合保留信号项（不被 _has_verifiable_evidences 门槛丢弃，且不与正式证据重复）
- 历史空壳信号识别与过滤
"""

from app.core.collusion.analyzer_parts.dimension_signals import (
    is_hollow_dimension_signal,
    synthesize_dimension_signal_findings,
)
from app.core.collusion.analyzer_parts.findings import _extract_structured_findings
from app.core.collusion.evidence_fusion import EvidenceFusionEngine


def _covered_dims(findings: list[dict]) -> set[str]:
    return {str(f.get("dimension")).upper() for f in findings if f.get("dimension")}


def test_no_hollow_signal_when_score_only() -> None:
    """: 仅有分数、无可核对实质内容 → 不合成空壳信号。"""
    compare_result = {
        "pair_id": "1:2",
        "d3_text": {"score": 0.88, "certainty": 0.2, "findings": []},
        "d4_price": {"score": 0.8, "certainty": 0.7, "findings": []},
    }
    signals = synthesize_dimension_signal_findings(compare_result, [], 1, 2)
    assert signals == []


def test_is_hollow_dimension_signal_detects_legacy_template() -> None:
    """历史空壳模板可被识别；带实质内容的信号项不被误伤。"""
    hollow = {
        "is_dimension_signal": True,
        "description": (
            "D3 技术方案雷同：结构化/算法分析检出信号（得分 88%、确定性 20%），"
            "但确定性低于阈值，未形成可展示依据，建议结合原文与综合研判人工核对。"
        ),
        "detail": "该维度得分 88%、确定性 20%，但确定性低于阈值，未形成可展示依据。",
        "evidences": [],
    }
    substance = {
        "is_dimension_signal": True,
        "description": "D4 报价异常：两家投标总价相差恰好固定比例 5%",
        "detail": "陪标特征",
        "evidences": [{"location": "第8页"}],
    }
    assert is_hollow_dimension_signal(hollow) is True
    assert is_hollow_dimension_signal(substance) is False
    assert is_hollow_dimension_signal({"description": hollow["description"]}) is False


def test_view_build_finding_items_filters_hollow_signals() -> None:
    """API 展示层过滤历史空壳信号，保留有实质内容的维度信号。"""
    from types import SimpleNamespace

    from app.services.collusion_view_service import build_finding_items

    r = SimpleNamespace(
        findings=[
            {
                "dimension": "D3",
                "level": "unknown",
                "description": (
                    "D3 技术方案雷同：结构化/算法分析检出信号（得分 88%、确定性 20%），"
                    "但确定性低于阈值，未形成可展示依据"
                ),
                "detail": "空壳",
                "evidences": [],
                "certainty": 0.2,
                "is_dimension_signal": True,
            },
            {
                "dimension": "D4",
                "level": "unknown",
                "description": "D4 报价异常：两家总价固定比例差",
                "detail": "陪标特征",
                "evidences": [{"location": "第3页"}],
                "certainty": 0.6,
                "is_dimension_signal": True,
            },
        ],
        llm_analysis=None,
    )
    items = build_finding_items(r, 1, 2, "A.pdf", "B.pdf", "1:2")
    assert len(items) == 1
    assert items[0].dimension == "D4"
    assert items[0].is_dimension_signal is True


def test_signal_reuses_dimension_substance_when_available() -> None:
    """短板补齐：维度有具体分析(summary/detail)时，信号正文复用其内容而非套话。"""
    compare_result = {
        "pair_id": "1:2",
        "pair_ref": {"doc_a_name": "A.pdf", "doc_b_name": "B.pdf"},
        "d4_price": {
            "score": 0.9,
            "certainty": 0.6,
            "findings": [{
                "summary": "两家投标总价相差恰好固定比例 5%，疑似协商报价",
                "detail": "甲报价 980 万、乙报价 1029 万，差额比例稳定，符合陪标特征。",
                "page_a": "第8页",
            }],
        },
    }
    signals = synthesize_dimension_signal_findings(compare_result, [], 1, 2)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["dimension"] == "D4"
    # 正文必须包含维度的具体分析内容，而非仅"无原文引用"套话
    assert "固定比例" in sig["description"]
    assert "陪标特征" in sig["detail"]
    assert "结构化/算法分析检出信号" not in sig["description"]
    # 单边页码定位应被带出，供用户跳转核对
    assert any(ev.get("location") == "第8页" for ev in sig["evidences"])


def test_signal_excludes_negative_and_speculative_findings() -> None:
    """反向定性/推测性 finding 不得作为信号正文；排除后无实质则不合成。"""
    compare_result = {
        "pair_id": "1:2",
        "pair_ref": {"doc_a_name": "A.pdf", "doc_b_name": "B.pdf"},
        "d4_price": {
            "score": 0.5,
            "certainty": 0.5,
            "findings": [
                {"summary": "未发现报价串通线索", "_negative_finding": True},
                {"summary": "可能存在协同，假设双方沟通过", "_speculative_finding": True},
            ],
        },
    }
    signals = synthesize_dimension_signal_findings(compare_result, [], 1, 2)
    # 排除后无可核对实质 → 不合成空壳
    assert signals == []


def test_no_signal_for_covered_or_zero_dimension() -> None:
    """已有正式证据的维度不重复出信号；得分为 0 的维度不出信号。"""
    compare_result = {
        "pair_id": "1:2",
        "d3_text": {"score": 0.9, "certainty": 0.9, "findings": []},
        "d5_qualification": {"score": 0.0, "certainty": 0.0, "findings": []},
    }
    structured = [{"dimension": "D3", "level": "second", "description": "正式证据"}]

    signals = synthesize_dimension_signal_findings(compare_result, structured, 1, 2)

    assert signals == []


def test_scored_dims_with_substance_or_evidence_are_displayable() -> None:
    """护栏：有正式证据或可核对实质的得分维度，extract+synthesize 后必可见。

 纯分数、无实质的维度（如 D7 仅 score）不再强制生成空壳条目；
 其分数仍由维度得分条/重合片段承载。
 """
    compare_result = {
        "pair_id": "1:2",
        "pair_ref": {"doc_a_name": "A.pdf", "doc_b_name": "B.pdf"},
        # D3：带可复核引文 → 正式证据幸存
        "d3_text": {
            "score": 0.9, "certainty": 0.9, "evidence_level": "second",
            "findings": [{
                "summary": "技术方案雷同", "detail": "同段落",
                "quote_a": "完全一致的施工组织方案", "page_a": "第12页",
                "quote_b": "完全一致的施工组织方案", "page_b": "第15页",
            }],
        },
        # D4：仅页码无引文 → 正式证据被剥离，但有实质 → 信号项
        "d4_price": {
            "score": 0.7, "certainty": 0.6, "evidence_level": "second",
            "findings": [{"summary": "报价规律", "page_a": "第3页"}],
        },
        # D7：有得分但无 findings → 不强制出空壳
        "d7_abnormal": {"score": 0.5, "certainty": 0.5, "findings": []},
    }

    structured = _extract_structured_findings(compare_result, 1, 2)
    signals = synthesize_dimension_signal_findings(compare_result, structured, 1, 2)
    all_findings = structured + signals

    assert "D3" in _covered_dims(all_findings)
    assert "D4" in _covered_dims(all_findings)
    assert "D7" not in _covered_dims(all_findings)
    assert all(not is_hollow_dimension_signal(f) for f in all_findings)


def test_evidence_fusion_preserves_dimension_signal() -> None:
    """群组融合保留维度信号项（无引文也不丢弃），且不与正式证据重复维度。"""
    engine = EvidenceFusionEngine({1: "A.pdf", 2: "B.pdf"})
    fused = engine.fuse_for_group(
        member_doc_ids=[1, 2],
        related_pair_ids=["1:2"],
        pair_findings_map={
            "1:2": [
                {
                    "dimension": "D3", "level": "second", "description": "文本雷同",
                    "evidences": [{
                        "doc_id": 1, "doc_name": "A.pdf",
                        "quote": "完全一致的技术方案", "location": "第6页",
                    }],
                },
                {
                    "dimension": "D4", "level": "unknown",
                    "description": "D4 报价异常：两家投标总价相差固定比例",
                    "detail": "陪标特征",
                    "evidences": [{"location": "第3页"}], "is_dimension_signal": True,
                },
            ],
        },
    )

    dims = _covered_dims(fused)
    assert "D3" in dims and "D4" in dims
    d4 = next(f for f in fused if f["dimension"] == "D4")
    assert d4["is_dimension_signal"] is True
    assert d4["evidences"]


def test_fusion_signal_not_duplicated_when_real_evidence_exists() -> None:
    """同一维度既有正式证据又有信号项时，群组级只保留正式证据，不出信号。"""
    engine = EvidenceFusionEngine({1: "A.pdf", 2: "B.pdf"})
    fused = engine.fuse_for_group(
        member_doc_ids=[1, 2],
        related_pair_ids=["1:2"],
        pair_findings_map={
            "1:2": [
                {
                    "dimension": "D3", "level": "second", "description": "文本雷同",
                    "evidences": [{
                        "doc_id": 1, "doc_name": "A.pdf",
                        "quote": "完全一致的技术方案", "location": "第6页",
                    }],
                },
                {
                    "dimension": "D3", "level": "unknown",
                    "description": "D3 信号项", "detail": "x",
                    "evidences": [], "is_dimension_signal": True,
                },
            ],
        },
    )

    d3_items = [f for f in fused if f["dimension"] == "D3"]
    assert len(d3_items) == 1
    assert not d3_items[0].get("is_dimension_signal")
