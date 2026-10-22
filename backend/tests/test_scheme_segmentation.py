"""评分方案切分器防御测试（, 阶段2）。

职责: 锁定 scheme 切分的关键行为——闭合后新表开新方案、副本指纹不切分、
 未闭合不切分、加权制按卷基准闭合判定、多方案核计报告分组。
不做: 不测完整提取链路(sample_corpus golden 职责)、不测 LLM。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.scheme_segmentation import (  # noqa: E402
    SchemeSegmenter,
    scheme_display_label,
    stamp_items_scheme,
)
from app.core.extractor.scoring.score_totals import (  # noqa: E402
    ScoringTotalsEntry,
    build_totals_report_payload,
)

pytestmark = pytest.mark.scoring


def _item(score: float, scheme: str | None = None, volume: str = "technical") -> SimpleNamespace:
    return SimpleNamespace(score_weight=score, scheme=scheme, volume=volume)


def test_new_scheme_opens_after_closure() -> None:
    """样本9形态: 前表闭合100后, 新指纹新表 → 新方案。"""
    segmenter = SchemeSegmenter()
    survivors: list = []
    first = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-a", incoming_item_count=11,
        current_survivors=survivors, volume_defs={},
    )
    assert first == "scheme_1"
    survivors = [_item(100.0, scheme="scheme_1")]
    second = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-b", incoming_item_count=3,
        current_survivors=survivors, volume_defs={},
    )
    assert second == "scheme_2"
    assert segmenter.scheme_count == 2


def test_duplicate_fingerprint_does_not_open_new_scheme() -> None:
    """样本4形态: 同一评分表副本(指纹相同)禁止切成'另一套方案'合法化重复计分。"""
    segmenter = SchemeSegmenter()
    segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-same", incoming_item_count=6,
        current_survivors=[], volume_defs={},
    )
    survivors = [_item(100.0, scheme="scheme_1")]
    again = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-same", incoming_item_count=6,
        current_survivors=survivors, volume_defs={},
    )
    assert again == "scheme_1"
    assert segmenter.scheme_count == 1


def test_unclosed_scheme_does_not_split() -> None:
    """样本18形态: 商务20+技术20+价格60分三表, 前表未闭合时禁止切分。"""
    segmenter = SchemeSegmenter()
    segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-1", incoming_item_count=5,
        current_survivors=[], volume_defs={},
    )
    survivors = [_item(20.0, scheme="scheme_1")]
    second = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-2", incoming_item_count=7,
        current_survivors=survivors, volume_defs={},
    )
    assert second == "scheme_1"


def test_declared_volume_without_facts_blocks_scheme_split() -> None:
    """项目1700形态(伴生): 框架表price事实100分自身凑满,
 但同表已声明technical卷(40%权重)明细在后页——方案未闭合, 禁止切分。"""
    segmenter = SchemeSegmenter()
    segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-framework", incoming_item_count=1,
        current_survivors=[], volume_defs={},
    )
    survivors = [_item(100.0, scheme="scheme_1", volume="price")]
    volume_defs = {
        "technical": SimpleNamespace(volume_max=100.0),
        "price": SimpleNamespace(volume_max=100.0),
    }
    second = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-detail", incoming_item_count=16,
        current_survivors=survivors, volume_defs=volume_defs,
    )
    assert second == "scheme_1"
    assert segmenter.scheme_count == 1


def test_weighted_volume_closure_counts_as_closed() -> None:
    """样本16形态: 三卷各100(卷基准闭合)视为方案闭合。"""
    segmenter = SchemeSegmenter()
    segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-1", incoming_item_count=10,
        current_survivors=[], volume_defs={},
    )
    survivors = [
        _item(100.0, scheme="scheme_1", volume="technical"),
        _item(100.0, scheme="scheme_1", volume="commercial"),
    ]
    volume_defs = {
        "technical": SimpleNamespace(volume_max=100.0),
        "commercial": SimpleNamespace(volume_max=100.0),
    }
    second = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-2", incoming_item_count=4,
        current_survivors=survivors, volume_defs=volume_defs,
    )
    assert second == "scheme_2"


def test_stamp_items_scheme_sets_field() -> None:
    items = [_item(5.0), _item(10.0)]
    stamp_items_scheme(items, "scheme_1")
    assert all(item.scheme == "scheme_1" for item in items)


def test_scheme_display_label() -> None:
    assert scheme_display_label("scheme_2", total_schemes=2) == "评分方案2"
    assert scheme_display_label("scheme_1", total_schemes=1) == ""
    assert scheme_display_label(None, total_schemes=3) == ""


def test_multi_scheme_totals_payload_groups_independently() -> None:
    """两套各100的方案核计通过, 禁止相加成200误报。"""
    entries = [
        ScoringTotalsEntry(score_weight=60.0, volume="technical", scheme="scheme_1"),
        ScoringTotalsEntry(score_weight=40.0, volume="price", scheme="scheme_1"),
        ScoringTotalsEntry(score_weight=100.0, volume="commercial", scheme="scheme_2"),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is False
    assert "评分方案1" in payload["note"] and "评分方案2" in payload["note"]
    assert payload["by_lot"] == {"评分方案1": 100.0, "评分方案2": 100.0}


def test_multi_scheme_totals_payload_reports_gap_per_scheme() -> None:
    entries = [
        ScoringTotalsEntry(score_weight=90.0, volume="technical", scheme="scheme_1"),
        ScoringTotalsEntry(score_weight=100.0, volume="commercial", scheme="scheme_2"),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is True
    assert any("评分方案1" in line for line in payload["diagnostics"])


# ---------------------------------------------------------------------------
# 结构边界 scheme_boundary_id 防御测试
# ---------------------------------------------------------------------------


def test_structural_boundary_advances_scheme_without_closure() -> None:
    """模板边界推进：新 scheme_boundary_id 到来时无需等闭合即推进。

    业务场景: p24 模板（JS-GCKYSJ01）与 p25 模板（JS-GCSJ01）为独立方案，
    即使 p24 的评分项未凑满100分，p25 也应开启新方案。
    """
    segmenter = SchemeSegmenter()
    segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-a",
        incoming_item_count=6,
        current_survivors=[],
        volume_defs={},
        scheme_boundary_id="tb:JS-GCKYSJ01@p24",
    )
    survivors_with_unclosed = [_item(50.0, scheme="scheme_1")]
    result = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-b",
        incoming_item_count=5,
        current_survivors=survivors_with_unclosed,
        volume_defs={},
        scheme_boundary_id="tb:JS-GCSJ01@p25",
    )
    assert result == "scheme_2", (
        f"模板边界切分：p25 新边界应开启 scheme_2，实际: {result}"
    )
    assert segmenter.scheme_count == 2


def test_structural_boundary_same_id_no_advance() -> None:
    """同一模板内多 chunk 不推进（相同 boundary_id）。"""
    segmenter = SchemeSegmenter()
    segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-a",
        incoming_item_count=5,
        current_survivors=[],
        volume_defs={},
        scheme_boundary_id="tb:JS-GCKYSJ01@p24",
    )
    result = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-a2",
        incoming_item_count=5,
        current_survivors=[_item(60.0, scheme="scheme_1")],
        volume_defs={},
        scheme_boundary_id="tb:JS-GCKYSJ01@p24",
    )
    assert result == "scheme_1", f"相同边界 ID 不应推进方案，实际: {result}"


def test_structural_boundary_first_chunk_no_advance_without_facts() -> None:
    """首个 chunk（无既有事实）时即使有新边界也不推进（从 scheme_1 开始）。"""
    segmenter = SchemeSegmenter()
    result = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint="fp-a",
        incoming_item_count=5,
        current_survivors=[],
        volume_defs={},
        scheme_boundary_id="tb:JS-GCKYSJ01@p24",
    )
    assert result == "scheme_1", f"首 chunk 无事实，应从 scheme_1 开始，实际: {result}"
    assert segmenter.scheme_count == 1


# ---------------------------------------------------------------------------
# C3: A→B→A 回访复用 scheme_1（boundary→scheme 映射）
# ---------------------------------------------------------------------------


def test_c3_a_b_a_revisit_reuses_scheme_1() -> None:
    """C3 RED→GREEN: A→B→A 模式第三次 A 复用 scheme_1，不建 scheme_3。"""
    seg = SchemeSegmenter()
    s1 = seg.assign_scheme_for_chunk(
        chunk_fingerprint="fp-a1", incoming_item_count=5,
        current_survivors=[], volume_defs={},
        scheme_boundary_id="tb:JS-GCKYSJ01",
    )
    assert s1 == "scheme_1"
    survivors_1 = [_item(50.0, scheme="scheme_1")]

    s2 = seg.assign_scheme_for_chunk(
        chunk_fingerprint="fp-b1", incoming_item_count=5,
        current_survivors=survivors_1, volume_defs={},
        scheme_boundary_id="tb:JS-GCSJ01",
    )
    assert s2 == "scheme_2"
    survivors_2 = survivors_1 + [_item(50.0, scheme="scheme_2")]

    s3 = seg.assign_scheme_for_chunk(
        chunk_fingerprint="fp-a2", incoming_item_count=5,
        current_survivors=survivors_2, volume_defs={},
        scheme_boundary_id="tb:JS-GCKYSJ01",
    )
    assert s3 == "scheme_1", f"回访 A 应复用 scheme_1，实际: {s3}"
    assert seg.scheme_count == 2, f"A→B→A 只应有 2 个方案，实际: {seg.scheme_count}"


# ---------------------------------------------------------------------------
# C4: 相同指纹跨边界不开新 scheme
# ---------------------------------------------------------------------------


def test_c4_duplicate_fingerprint_across_boundary_no_advance() -> None:
    """C4 RED→GREEN: 同指纹但不同边界 ID → 副本检测优先，不推进 scheme。"""
    seg = SchemeSegmenter()
    s1 = seg.assign_scheme_for_chunk(
        chunk_fingerprint="fp-dup", incoming_item_count=5,
        current_survivors=[], volume_defs={},
        scheme_boundary_id="tb:JS-GCKYSJ01",
    )
    assert s1 == "scheme_1"
    survivors = [_item(80.0, scheme="scheme_1")]

    s2 = seg.assign_scheme_for_chunk(
        chunk_fingerprint="fp-dup", incoming_item_count=5,
        current_survivors=survivors, volume_defs={},
        scheme_boundary_id="tb:JS-GCSJ01",  # 新边界，但指纹同
    )
    assert s2 == "scheme_1", f"同指纹即使边界变化应视为副本，实际: {s2}"
