"""D3 防御测试 — D1/D2 全局发现注入到 cluster.aggregated_evidence 的双向同步契约。

覆盖 同源治理：以前 enrich_clusters_with_global_findings 跳过 D1/D2
仅处理 D4/D8，导致左侧"全局发现"展示了 D2 关联但 router 在
`_build_global_finding_list`（filter source=global_features）中找不到对应条目。

本测试验证：
- 当 global_features.global_findings 含 personnel_overlap (D1) 时，
 覆盖的 cluster 应在 aggregated_evidence 中出现一条 source='global_features'
 的 D1 evidence_item。
- 同理 company_relation (D2)、pricing_pattern (D4)、metadata_cluster (D8)
 全部走同一注入路径。
- 未被任何 cluster 覆盖的 finding 进入返回的 extra 列表。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.core.collusion.graph_cluster import (
    CollusionCluster,
    enrich_clusters_with_global_findings,
)


@dataclass
class _StubGlobalFeatures:
    """最小化 stub，模拟 GlobalFeatureResult 的 global_findings 字段。"""
    global_findings: list[dict] = field(default_factory=list)


def _make_cluster(group_index: int, members: list[int]) -> CollusionCluster:
    # group_index 仅用于测试可读性；CollusionCluster 数据类未暴露该字段
    _ = group_index
    return CollusionCluster(
        member_doc_ids=members,
        risk_level="medium",
        member_names=[f"文件{m}" for m in members],
    )


class TestD1GlobalFindingInjection:
    def test_d1_personnel_overlap_injected(self):
        clusters = [_make_cluster(0, [101, 102])]
        gf = _StubGlobalFeatures(global_findings=[{
            "type": "personnel_overlap",
            "involved_doc_ids": [101, 102],
            "involved_names": ["文件101", "文件102"],
            "description": '关键人员<PERSON_A>同时出现在 文件101 与 文件102',
        }])

        extra = enrich_clusters_with_global_findings(
            clusters, gf, doc_names={101: "文件101", 102: "文件102"},
        )

        assert extra == []
        evidence = clusters[0].aggregated_evidence or []
        assert len(evidence) == 1
        item = evidence[0]
        assert item["source"] == "global_features"
        assert item["dimension"] == "D1"
        assert item["finding_type"] == "personnel_overlap"
        assert item["involved_doc_ids"] == [101, 102]


class TestD2GlobalFindingInjection:
    def test_d2_company_relation_injected(self):
        """关键案例：图 5 反馈的'统一社会信用代码完全一致或登记前缀一致'。

        修复后这条 D2 全局发现不再被 enrich 跳过。
        """
        clusters = [_make_cluster(0, [201, 202])]
        gf = _StubGlobalFeatures(global_findings=[{
            "type": "company_relation",
            "involved_doc_ids": [201, 202],
            "involved_names": ["A 公司投标书", "B 公司投标书"],
            "description": "统一社会信用代码完全一致：91110000XXXXX",
        }])

        enrich_clusters_with_global_findings(clusters, gf, doc_names={201: "A", 202: "B"})

        evidence = clusters[0].aggregated_evidence or []
        assert len(evidence) == 1
        assert evidence[0]["dimension"] == "D2"
        assert evidence[0]["finding_type"] == "company_relation"
        assert evidence[0]["source"] == "global_features"


class TestD4D8StillWork:
    def test_d4_pricing_pattern_injected(self):
        clusters = [_make_cluster(0, [301, 302])]
        gf = _StubGlobalFeatures(global_findings=[{
            "type": "pricing_pattern",
            "involved_doc_ids": [301, 302],
            "description": "报价呈精确比例 0.95",
        }])
        enrich_clusters_with_global_findings(clusters, gf)
        evidence = clusters[0].aggregated_evidence or []
        assert any(e["dimension"] == "D4" for e in evidence)

    def test_d8_metadata_cluster_injected(self):
        clusters = [_make_cluster(0, [401, 402])]
        gf = _StubGlobalFeatures(global_findings=[{
            "type": "metadata_cluster",
            "involved_doc_ids": [401, 402],
            "description": "PDF 创建工具完全一致",
        }])
        enrich_clusters_with_global_findings(clusters, gf)
        evidence = clusters[0].aggregated_evidence or []
        assert any(e["dimension"] == "D8" for e in evidence)


class TestExtraDriftingFindings:
    def test_orphan_finding_returns_to_extra(self):
        """涉及 doc 不在任何 cluster 中 → 进入 extra 返回值。"""
        clusters = [_make_cluster(0, [501, 502])]
        gf = _StubGlobalFeatures(global_findings=[{
            "type": "company_relation",
            "involved_doc_ids": [999, 998],  # 与 cluster 无交集
            "description": "孤立关联",
        }])
        extra = enrich_clusters_with_global_findings(clusters, gf)
        assert len(extra) == 1
        assert extra[0]["type"] == "company_relation"
        assert extra[0]["involved_doc_ids"] == [999, 998]

    def test_unknown_type_skipped(self):
        clusters = [_make_cluster(0, [601, 602])]
        gf = _StubGlobalFeatures(global_findings=[{
            "type": "unrecognized_type",
            "involved_doc_ids": [601, 602],
            "description": "...",
        }])
        extra = enrich_clusters_with_global_findings(clusters, gf)
        assert extra == []
        assert not (clusters[0].aggregated_evidence or [])


class TestEmptyInputs:
    def test_no_findings(self):
        clusters = [_make_cluster(0, [1, 2])]
        gf = _StubGlobalFeatures(global_findings=[])
        extra = enrich_clusters_with_global_findings(clusters, gf)
        assert extra == []

    def test_no_global_features(self):
        clusters = [_make_cluster(0, [1, 2])]
        extra = enrich_clusters_with_global_findings(clusters, None)
        assert extra == []
