""" 防御测试：EvidenceFacet 分面契约（构建/合并/覆盖/保底选择）。

覆盖风险点:
1. 复合要求（QHSE"须包含"长句、多主体证书句）必须拆出并列分面；
2. 首轮与深搜证据合并必须单调不减且保留首次命中角色；
3. 多实体要求只命中部分实体时，覆盖评估必须报告缺口（词桶存在性不够）；
4. 分面保底选择：单一强势分面不得挤掉其他分面的唯一证据。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.retriever.evidence_coverage import (
    EvidenceFacet,
    build_evidence_facets,
    evaluate_facet_coverage,
    merge_search_results,
    select_facet_balanced_results,
)
from app.core.retriever.semantic_retriever import SearchResult

_QHSE_DESC = (
    "投标文件须包含完整的QHSE组织机构、HSE作业计划书、施工风险初步评估、"
    "风险识别与削减措施、应急方案、防护设备设施配置及安全费用使用计划等材料"
)
_STAFF_DESC = "项目经理、技术负责人及安全生产管理人员均须提供相应资格证书"


def _sr(
    chunk_id: int,
    content: str,
    similarity: float = 0.5,
    role: str | None = None,
    sub_query: str | None = None,
    page: int = 1,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=1,
        content=content,
        page_num=page,
        similarity=similarity,
        role=role,
        matched_sub_query=sub_query,
    )


class TestBuildEvidenceFacets:
    def test_qhse_compound_description_produces_parallel_facets(self):
        facets = build_evidence_facets(_QHSE_DESC, [])
        keys = " ".join(f.key for f in facets)
        assert len(facets) >= 4, f"复合并列项未形成多个分面: {facets}"
        assert "防护设备设施配置" in keys
        assert "安全费用使用计划" in keys
        assert "应急方案" in keys

    def test_multi_subject_description_produces_per_entity_facets(self):
        facets = build_evidence_facets(_STAFF_DESC, [])
        keys = " ".join(f.key for f in facets)
        assert "项目经理" in keys
        assert "技术负责人" in keys
        assert "安全生产管理人员" in keys

    def test_sub_requirements_become_dedicated_facets(self):
        facets = build_evidence_facets(
            "项目管理机构关键岗位人员配置齐全",
            ["施工组长特种作业证", "现场安全监护人证书"],
        )
        keys = " ".join(f.key for f in facets)
        assert "施工组长特种作业证" in keys
        assert "现场安全监护人证书" in keys

    def test_facets_deduplicated_and_queries_nonempty(self):
        facets = build_evidence_facets("投标人须提供营业执照", ["营业执照", "营业执照"])
        keys = [f.key for f in facets]
        assert len(keys) == len(set(keys)), f"分面键重复: {keys}"
        assert all(f.query.strip() for f in facets)


class TestMergeSearchResults:
    def test_union_is_monotonic_and_deduplicated(self):
        primary = [_sr(1, "首轮证据A", role="primary"), _sr(2, "首轮证据B", role="primary")]
        deep = [_sr(2, "深搜重复B", role="deep"), _sr(3, "深搜新增C", role="deep")]
        merged = merge_search_results(primary, deep)
        assert [r.chunk_id for r in merged] == [1, 2, 3]

    def test_first_occurrence_role_preserved(self):
        primary = [_sr(7, "证据", role="primary", similarity=0.4)]
        deep = [_sr(7, "证据", role="deep", similarity=0.9)]
        merged = merge_search_results(primary, deep)
        assert len(merged) == 1
        assert merged[0].role == "primary", "重复chunk必须保留首轮角色"

    def test_empty_groups_are_safe(self):
        assert merge_search_results([], []) == []
        only_deep = merge_search_results([], [_sr(5, "深搜", role="deep")])
        assert [r.chunk_id for r in only_deep] == [5]


class TestFacetCoverage:
    def test_partial_entity_hit_reports_missing_facets(self):
        """三名人员只命中一人：覆盖评估必须报缺口（旧词桶只看'证书'出现即满足）。"""
        facets = build_evidence_facets(_STAFF_DESC, [])
        pm_facet = next(f for f in facets if "项目经理" in f.key)
        results = [_sr(1, '项目经理<PERSON_A>持有一级建造师注册证书', sub_query=pm_facet.query)]
        cov = evaluate_facet_coverage(facets, results)
        assert cov.missing_keys, "仅命中项目经理时必须存在未覆盖分面"
        missing_joined = " ".join(cov.missing_keys)
        assert "技术负责人" in missing_joined or "安全生产管理人员" in missing_joined

    def test_full_coverage_reports_no_missing(self):
        facets = build_evidence_facets(_STAFF_DESC, [])
        results = [
            _sr(i + 1, f"{f.key} 资格证书扫描件", sub_query=f.query)
            for i, f in enumerate(facets)
        ]
        cov = evaluate_facet_coverage(facets, results)
        assert cov.missing_keys == ()

    def test_content_literal_hit_counts_as_covered(self):
        """无 matched_sub_query 的命中（图片/目录）按内容字面判定覆盖。"""
        facets = build_evidence_facets(_QHSE_DESC, [])
        target = next(f for f in facets if "安全费用使用计划" in f.key)
        results = [_sr(9, "第498页 安全费用使用计划表：费用项目、金额、使用时间")]
        cov = evaluate_facet_coverage(facets, results)
        assert target.key in cov.covered_keys

    def test_empty_facets_mean_no_missing(self):
        cov = evaluate_facet_coverage([], [_sr(1, "任意内容")])
        assert cov.expected_keys == ()
        assert cov.missing_keys == ()


class TestSelectFacetBalanced:
    def test_each_facet_floor_before_global_fill(self):
        """强势分面多条高分候选不得挤掉弱势分面的唯一证据。"""
        facet_a = EvidenceFacet(key="个人防护用品", query="个人防护用品")
        facet_b = EvidenceFacet(key="安全专项费用", query="安全专项费用")
        results = [
            _sr(1, "个人防护用品一览表 安全帽", similarity=0.9, sub_query="个人防护用品"),
            _sr(2, "个人防护用品 电焊工手套", similarity=0.85, sub_query="个人防护用品"),
            _sr(3, "个人防护用品 安全带", similarity=0.8, sub_query="个人防护用品"),
            _sr(4, "安全专项费用保障计划", similarity=0.3, sub_query="安全专项费用"),
        ]
        selected = select_facet_balanced_results(results, [facet_a, facet_b], limit=3)
        ids = {r.chunk_id for r in selected}
        assert 4 in ids, "弱势分面的唯一证据必须保底入选"
        assert 1 in ids, "强势分面的最佳证据保留"
        assert len(selected) == 3

    def test_limit_respected_and_order_stable(self):
        facet = EvidenceFacet(key="营业执照", query="营业执照")
        results = [
            _sr(i, f"营业执照相关内容{i}", similarity=1.0 - i * 0.1, sub_query="营业执照")
            for i in range(1, 6)
        ]
        selected = select_facet_balanced_results(results, [facet], limit=2)
        assert [r.chunk_id for r in selected] == [1, 2]

    def test_no_facets_falls_back_to_similarity_order(self):
        results = [_sr(1, "A", similarity=0.2), _sr(2, "B", similarity=0.9)]
        selected = select_facet_balanced_results(results, [], limit=2)
        assert [r.chunk_id for r in selected] == [2, 1]
