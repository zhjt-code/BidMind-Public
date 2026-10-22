""" 工作包 2C 防御测试: 审查证据充分性判定与分面保底原文选择。

职责: 锁定 evidence_policy.should_search_independently 的触发矩阵与
 select_facet_covered_rows 的分面保底/去重/退化排序行为。
不做: 不连 DB、不调 LLM、不测 Prompt 拼接（后者归 test_review_raw_evidence_injection）。

历史教训映射:
 (a) 独立补搜此前仅在"否决项且来源页邻页没有任何图片文本"时触发——任意无关
 邻页图片都会抑制全局补搜, 非否决项永远无补救;
 (b) 原文佐证固定按 role/similarity 取前3条——同一分面的3条高分命中会挤掉
 其他分面的唯一证据。
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.core.ai.reviewer_parts.evidence_policy import (
    EvidenceAdequacy,
    select_facet_covered_rows,
    should_search_independently,
)
from app.core.retriever.evidence_coverage import EvidenceFacet

pytestmark = pytest.mark.scoring

# 三个互斥分面: A/B 走 matched_sub_query 精确命中, C 走 content 字面探针命中
FACET_A = EvidenceFacet(key="资质证书", query="投标人 资质证书")
FACET_B = EvidenceFacet(key="项目业绩", query="投标人 项目业绩")
FACET_C = EvidenceFacet(key="安全员社保", query="安全员 社保缴费证明")

_LEGACY_ROLE_RANK = {"primary": 0, "deep": 1, "toc": 2, "image": 3}


def _row(
    chunk_id: Optional[int],
    role: str,
    similarity: float,
    content: str,
    sub_query: Optional[str] = None,
    page: int = 1,
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "role": role,
        "page": page,
        "similarity": similarity,
        "section": "",
        "content": content,
        "matched_sub_query": sub_query,
        "matched_keywords": None,
    }


def _adequacy(**overrides: Any) -> EvidenceAdequacy:
    """证据充分的基准状态; 单测按需覆写单一维度。"""
    base: dict[str, Any] = dict(
        has_response=True,
        is_reviewable=True,
        is_mandatory=False,
        has_source_pages=True,
        completeness="full",
        missing_elements=(),
        evidence_anchor="anchor#1",
        facet_missing_keys=(),
        has_local_image_evidence=False,
    )
    base.update(overrides)
    return EvidenceAdequacy(**base)


# ---------------------------------------------------------------------------
# select_facet_covered_rows: 分面保底
# ---------------------------------------------------------------------------


def test_each_facet_keeps_one_row_when_limit_three() -> None:
    """三分面各有命中时 limit=3 各保底一条: 分面A的3条高分不得挤掉B/C唯一证据。"""
    rows = [
        _row(1, "primary", 0.95, "资质证书扫描件甲", "投标人 资质证书"),
        _row(2, "primary", 0.93, "资质证书副本乙", "投标人 资质证书"),
        _row(3, "primary", 0.91, "资质证书说明丙", "投标人 资质证书"),
        _row(4, "deep", 0.42, "近三年项目业绩一览表", "投标人 项目业绩"),
        _row(5, "image", 0.10, "安全员社保缴纳记录扫描件"),
    ]
    selected = select_facet_covered_rows(rows, (FACET_A, FACET_B, FACET_C), 3)
    assert [r["chunk_id"] for r in selected] == [1, 4, 5]


def test_same_facet_competes_by_role_then_similarity() -> None:
    """同分面内按 role_rank 优先、similarity 次之竞争: primary 0.70 胜 deep 0.99。"""
    rows = [
        _row(1, "deep", 0.99, "资质证书深搜命中", "投标人 资质证书"),
        _row(2, "primary", 0.50, "资质证书正文命中", "投标人 资质证书"),
        _row(3, "primary", 0.70, "资质证书正文更优", "投标人 资质证书"),
    ]
    selected = select_facet_covered_rows(rows, (FACET_A,), 1)
    assert [r["chunk_id"] for r in selected] == [3]


def test_no_facets_degrades_to_legacy_role_similarity_order() -> None:
    """无分面时与旧 (role_rank, -similarity) 排序完全一致（兼容既有行为）。"""
    rows = [
        _row(1, "toc", 0.99, "目录导航一"),
        _row(2, "deep", 0.30, "深搜补充二"),
        _row(3, "primary", 0.55, "正文命中三"),
        _row(4, "primary", 0.80, "正文命中四"),
        _row(5, "image", 0.90, "图片证据五"),
    ]
    legacy = sorted(
        rows,
        key=lambda r: (_LEGACY_ROLE_RANK[r["role"]], -r["similarity"]),
    )
    assert select_facet_covered_rows(rows, (), 5) == legacy
    assert select_facet_covered_rows(rows, (), 3) == legacy[:3]


def test_second_round_fills_remaining_quota_globally() -> None:
    """第一轮每分面保底一条后, 剩余额度按全局 (role_rank, -similarity) 稳定补齐。"""
    rows = [
        _row(1, "primary", 0.90, "资质证书甲", "投标人 资质证书"),
        _row(2, "primary", 0.80, "资质证书乙", "投标人 资质证书"),
        _row(3, "deep", 0.70, "项目业绩丙", "投标人 项目业绩"),
        _row(4, "image", 0.60, "无关图片丁"),
    ]
    selected = select_facet_covered_rows(rows, (FACET_A, FACET_B), 3)
    # 第一轮: A→1, B→3; 第二轮全局: primary 0.80 (2) 优于 image (4)
    assert [r["chunk_id"] for r in selected] == [1, 3, 2]


def test_dedupe_by_chunk_id_then_normalized_content_prefix() -> None:
    """去重: 有 chunk_id 按 chunk_id; 无 chunk_id 按内容前60字规范化。"""
    rows = [
        _row(1, "primary", 0.9, "资质证书甲", "投标人 资质证书"),
        _row(1, "deep", 0.8, "资质证书甲重复命中", "投标人 资质证书"),
        _row(None, "primary", 0.7, "项目业绩  文本", "投标人 项目业绩"),
        _row(None, "deep", 0.6, "项目业绩 文本", "投标人 项目业绩"),
    ]
    selected = select_facet_covered_rows(rows, (), 10)
    assert len(selected) == 2
    assert [r["role"] for r in selected] == ["primary", "primary"]


def test_limit_zero_or_empty_rows_returns_empty() -> None:
    assert select_facet_covered_rows([], (FACET_A,), 3) == []
    assert select_facet_covered_rows([_row(1, "primary", 0.9, "资质证书")], (), 0) == []


# ---------------------------------------------------------------------------
# should_search_independently: 触发矩阵
# ---------------------------------------------------------------------------


def test_partial_completeness_triggers_even_with_local_image_evidence() -> None:
    """ 根因一: 邻页存在无关图片(has_local_image_evidence=True)不得抑制补搜。"""
    state = _adequacy(completeness="partial", has_local_image_evidence=True)
    assert should_search_independently(state) is True


def test_fully_covered_state_does_not_trigger() -> None:
    """完整+有页码+有anchor+无缺口 → 不补搜。"""
    assert should_search_independently(_adequacy()) is False


def test_not_reviewable_never_triggers() -> None:
    """非可审要求(is_reviewable=False)一律 False, 即使其余全部命中触发条件。"""
    state = _adequacy(
        is_reviewable=False,
        has_response=False,
        is_mandatory=True,
        has_source_pages=False,
        completeness="partial",
        missing_elements=("社保缴费证明",),
        evidence_anchor=None,
        facet_missing_keys=("安全员社保",),
    )
    assert should_search_independently(state) is False


def test_missing_response_triggers() -> None:
    assert should_search_independently(_adequacy(has_response=False)) is True


def test_non_mandatory_partial_triggers() -> None:
    """非否决项此前永远无补救, 现在 partial 同样触发。"""
    state = _adequacy(completeness="partial", is_mandatory=False)
    assert should_search_independently(state) is True


def test_form_only_completeness_triggers() -> None:
    assert should_search_independently(_adequacy(completeness="form_only")) is True


def test_missing_source_pages_triggers() -> None:
    assert should_search_independently(_adequacy(has_source_pages=False)) is True


def test_missing_elements_trigger() -> None:
    state = _adequacy(missing_elements=("专职安全员社保缴费证明",))
    assert should_search_independently(state) is True


def test_facet_gap_triggers() -> None:
    assert should_search_independently(
        _adequacy(facet_missing_keys=("安全员社保",))
    ) is True


def test_missing_anchor_triggers_only_for_mandatory() -> None:
    """anchor 缺失仅在否决项上构成触发条件。"""
    assert should_search_independently(
        _adequacy(evidence_anchor=None, is_mandatory=True)
    ) is True
    assert should_search_independently(
        _adequacy(evidence_anchor=None, is_mandatory=False)
    ) is False
