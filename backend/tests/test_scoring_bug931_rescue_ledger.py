""" 评分救援账本/卷映射/缺项反馈防御测试。

覆盖: 3427 分值构成括注组对与「得满分50」不得当文档总分、综合标卷键、
 header 未知卷 skip 不连坐、跨页折行缝合与点名缺项。
不做: 不调 LLM、不访问 DB、不改生产段 table parser。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.extractor.scoring.ledger.declaration_capture import (
    _ledger_flat_pairs,
    capture_scoring_declarations,
    declaration_grids_from_signals,
)
from app.core.extractor.scoring.vlm.rescue_missing_items import (
    format_missing_item_feedback,
    missing_scored_item_names,
    stitch_continuation_name_rows,
)
from app.core.extractor.scoring.vlm.rescue_rounds import (
    _build_retry_feedback,
    _volume_defs_from_header,
)
from app.core.extractor.scoring.weighted_volume_table import volume_from_scoring_label
from app.core.scoring_pdf_rescue_config import ScoringPdfRescueSettings

pytestmark = pytest.mark.scoring

_DECL_3427_CELLS = [
    "2.2.1",
    "分值构成 （总分100分）",
    "",
    "技术标(施工组织设计)：  35  分商务标：  50  分综合标：  15  分",
]


class TestDeclaration3427:
    def test_paren_subtitle_flat_pairs(self):
        pairs = _ledger_flat_pairs(
            "分值构成: 技术标(施工组织设计)：  35  分商务标：  50  分综合标：  15  分"
        )
        got = {p["label"]: p["score"] for p in pairs}
        assert got["技术标"] == 35.0
        assert got["商务标"] == 50.0
        assert got["综合标"] == 15.0

    def test_declaration_grid_captures_three_groups_and_total_100(self):
        grids = declaration_grids_from_signals(
            {"declaration_table_rows": [{"page": 2, "cells": _DECL_3427_CELLS}]}
        )
        page_text = (
            "相等者得满分 50 分。\n"
            "分值构成 （总分100分）\n"
            "技术标(施工组织设计)：35分商务标：50分综合标：15分"
        )
        capture = capture_scoring_declarations([(2, page_text)], grids)
        got = {(p.norm_label, p.score) for p in capture.pairs}
        assert ("技术", 35.0) in got
        assert ("商务", 50.0) in got
        assert ("综合", 15.0) in got
        assert round(sum(p.score for p in capture.pairs), 4) == 100.0
        assert 100.0 in capture.total_candidates
        assert 50.0 not in capture.total_candidates


class TestVolumeComposite:
    def test_composite_bid_maps_to_composite_volume(self):
        assert volume_from_scoring_label("综合标") == "composite"
        assert volume_from_scoring_label("综合部分") == "composite"
        assert volume_from_scoring_label("技术标") == "technical"
        assert volume_from_scoring_label("商务标") == "commercial"

    def test_header_three_volumes_including_composite(self):
        header = {
            "volume_totals": [
                {"label": "技术标", "total": 35},
                {"label": "商务标", "total": 50},
                {"label": "综合标", "total": 15},
            ]
        }
        anchor = "技术标35分商务标50分综合标15分总分100分"
        defs = _volume_defs_from_header(header, anchor)
        assert set(defs) == {"technical", "commercial", "composite"}
        assert defs["composite"].volume_max == 15.0

    def test_unknown_volume_label_does_not_wipe_known(self):
        header = {
            "volume_totals": [
                {"label": "技术标", "total": 35},
                {"label": "商务标", "total": 50},
                {"label": "神秘评审", "total": 15},
            ]
        }
        anchor = "技术标35分商务标50分神秘评审15分"
        defs = _volume_defs_from_header(header, anchor)
        assert set(defs) == {"technical", "commercial"}


class TestMissingItemFeedback:
    def test_stitch_continuation_appends_short_orphan(self):
        rows = [
            ("环境保护、文明施工管理体系与", 5.0),
            ("技术措施", None),
            ("工程进度计划", 5.0),
        ]
        stitched = stitch_continuation_name_rows(rows)
        names = [name for name, _ in stitched]
        assert "环境保护、文明施工管理体系与技术措施" in names
        assert "技术措施" not in names
        assert "工程进度计划" in names

    def test_missing_names_named_in_feedback(self):
        chunk = SimpleNamespace(
            chunk_metadata={
                "scoring_payload": {
                    "grid": [
                        ["序号", "评审因素", "分值"],
                        ["1", "内容完整性和编制水平", "5"],
                        ["2", "施工方案与技术措施", "5"],
                        ["3", "环境保护、文明施工管理体系与", "5"],
                        ["", "技术措施", ""],
                        ["4", "工程进度计划", "5"],
                    ],
                    "column_roles": {"name": 1, "score": 2},
                }
            }
        )
        kept = [
            SimpleNamespace(score_item_name="内容完整性和编制水平"),
            SimpleNamespace(score_item_name="工程进度计划"),
        ]
        missing = missing_scored_item_names([chunk], kept)
        assert any("施工方案与技术措施" in name for name in missing)
        assert any("环境保护" in name and "技术措施" in name for name in missing)
        feedback = _build_retry_feedback(
            SimpleNamespace(feedback="核计90/100未闭合"),
            kept,
            [],
            [chunk],
        )
        assert "请补提" in feedback
        assert "施工方案与技术措施" in feedback
        assert format_missing_item_feedback(missing).startswith("请补提")


def test_pdf_rescue_default_max_rounds_is_three():
    assert ScoringPdfRescueSettings.model_fields["SCORING_PDF_RESCUE_MAX_ROUNDS"].default == 3
