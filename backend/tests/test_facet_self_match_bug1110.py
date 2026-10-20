"""分面覆盖判定不得把判定键自身当成待匹配正文。

问题形态: 覆盖判定把判定键混入待判定内容, 使判定恒真。
本案例夹具与同形态第二例使用不同分面实体。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.reviewer_parts.evidence_ledger.builder import collect_atoms_from_rows
from app.core.ai.reviewer_parts.evidence_ledger.contracts import facet_covers_text
from app.core.retriever.evidence_coverage import EvidenceFacet


def test_unrelated_text_is_not_covered_by_social_security_facet() -> None:
    facet = EvidenceFacet(key="社保缴费证明", query="投标人 社保缴费证明")
    assert facet_covers_text(facet, "完全无关的文本内容: 天气很好", "") is False
    assert facet_covers_text(facet, "", "") is False


def test_unrelated_text_is_not_covered_by_award_notice_facet() -> None:
    """同形态第二例: 换一个分面实体, 判定键仍不得自匹配。"""
    facet = EvidenceFacet(key="中标通知书", query="业绩 中标通知书")
    assert facet_covers_text(facet, "项目负责人身份证扫描件", "") is False
    assert facet_covers_text(facet, "", "") is False


def test_literal_and_tail_probe_still_match() -> None:
    facet = EvidenceFacet(key="社保缴费证明", query="投标人 社保缴费证明")
    assert facet_covers_text(facet, "附社保缴费证明扫描件一份", "") is True
    assert facet_covers_text(facet, "详见缴费证明原件", "") is True


def test_collect_atoms_stays_below_cartesian_product() -> None:
    """内容互不相关时, 原子数不得退化成 行数 x 分面数。"""
    facets = tuple(
        EvidenceFacet(key=f"专有材料{index}证明书", query=f"专有材料{index}证明书")
        for index in range(8)
    )
    rows = [
        {
            "bid_response_id": 1,
            "chunk_id": index,
            "page": index + 1,
            "document_id": 9,
            "matched_sub_query": "",
            "content": f"第{index}段只讨论天气与交通, 不含任何证明材料",
            "chunk_type": "text",
        }
        for index in range(12)
    ]
    ledger = collect_atoms_from_rows(rows, {1: facets}, {1: 41})
    cartesian = len(rows) * len(facets)
    assert len(ledger.atoms) < cartesian // 2
    assert len(ledger.atoms) == 0


def test_collect_atoms_keeps_real_hit() -> None:
    facets = (
        EvidenceFacet(key="社保缴费证明", query="社保缴费证明"),
        EvidenceFacet(key="中标通知书", query="中标通知书"),
    )
    rows = [{
        "bid_response_id": 1,
        "chunk_id": 7,
        "page": 3,
        "document_id": 9,
        "matched_sub_query": "",
        "content": "项目人员社保缴费证明详见附件",
        "chunk_type": "text",
    }]
    ledger = collect_atoms_from_rows(rows, {1: facets}, {1: 41})
    kinds = {atom.kind for atom in ledger.atoms}
    assert "社保缴费证明" in kinds
    assert "中标通知书" not in kinds
