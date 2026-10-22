# -*- coding: utf-8 -*-
""" 救援页窗锚定、目录假阳性与空分组项内归卷。

职责: 锁住 3424/3425 形态——救援页窗锚定、定位器拒目录、空分组项内标记粘性归卷、
 救援短标签在无卷定义时填卷键。
不做: 不读真实 PDF、不调 LLM/DB、不测拼表/门禁/落库。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.scoring_table_parser import (  # noqa: E402
    parse_scoring_table,
)
from app.core.extractor.scoring.vlm.candidate_contract import (  # noqa: E402
    VlmScoringItemDraft,
    to_extracted_items,
)
from app.core.extractor.scoring.vlm.rescue_input_builder import (  # noqa: E402
    collect_rescue_pages,
)
from app.core.extractor.scoring.vlm.scoring_page_locator import (  # noqa: E402
    locate_scoring_pages,
)
from app.core.extractor.scoring.volume_labeling import (  # noqa: E402
    volume_from_explicit_bid_label,
    volume_from_score_standard_marker,
)

pytestmark = pytest.mark.scoring

_TOC_PAGE = (
    "目录\n"
    "评标办法................................40\n"
    "2.2 分值构成与评分标准................46\n"
)
_REAL_SCORE_PAGE = "评分因素 标准分 服务方案 10分 服务质量管控 10分"


class TestCollectRescuePages:
    def test_3424_window_keeps_table_drops_toc(self) -> None:
        pages, _truncated = collect_rescue_pages(
            {48, 49, 50, 51},
            {3, 23, 47, 48, 51, 52},
            page_margin=1,
            max_pages=30,
            page_count=149,
        )
        kept = set(pages)
        assert {47, 48, 49, 50, 51} <= kept
        assert 3 not in kept
        assert 10 not in kept
        assert 26 not in kept

    def test_chunk_short_window_still_expands(self) -> None:
        pages, truncated = collect_rescue_pages(
            {5, 6, 7, 8, 9},
            {5, 10},
            page_margin=1,
            max_pages=30,
            page_count=10,
        )
        assert pages == [4, 5, 6, 7, 8, 9, 10]
        assert truncated is False

    def test_no_chunk_uses_densest_cluster(self) -> None:
        pages, _truncated = collect_rescue_pages(
            set(),
            {3, 23, 47, 48, 51, 52},
            page_margin=1,
            max_pages=30,
            page_count=149,
        )
        kept = set(pages)
        assert {47, 48, 51, 52} <= kept
        assert 3 not in kept


class TestLocatorRejectsToc:
    def test_dotted_toc_page_rejected(self) -> None:
        pages = locate_scoring_pages({3: _TOC_PAGE, 48: _REAL_SCORE_PAGE})
        assert 3 not in pages
        assert 48 in pages

    def test_real_scoring_page_without_leaders_kept(self) -> None:
        assert 48 in locate_scoring_pages({48: _REAL_SCORE_PAGE})


class TestItemVolumeMarkers:
    def test_explicit_phrases_map_to_volumes(self) -> None:
        assert volume_from_score_standard_marker("按公式计分。经济标评分标准") == (
            "price",
            "经济标评分标准",
        )
        assert volume_from_score_standard_marker("方案完整。技术标评分标准") == (
            "technical",
            "技术标评分标准",
        )
        assert volume_from_score_standard_marker("近三年业绩。资信业绩评分标准") == (
            "qualification",
            "资信业绩评分标准",
        )
        assert volume_from_score_standard_marker("人员资历。资信标评分标准") == (
            "qualification",
            "资信标评分标准",
        )

    def test_bare_keywords_do_not_invent_volume(self) -> None:
        assert volume_from_score_standard_marker("投标报价按公式计分") is None
        assert volume_from_score_standard_marker("企业业绩每项得1分") is None
        assert volume_from_score_standard_marker("技术方案") is None

    def test_empty_group_uses_item_markers(self) -> None:
        parsed = parse_scoring_table(
            "",
            section_path="第三章 评标办法",
            grid=[
                ["条款号", "评分因素", "标准分", "评分标准"],
                ["2.2.1", "投标报价", "10", "按插值法计算得分。经济标评分标准"],
                ["2.2.2", "服务方案", "10", "方案完整可行得满分。技术标评分标准"],
                ["2.2.3", "企业业绩", "5", "近三年类似业绩每项得1分。资信业绩评分标准"],
            ],
        )
        by_name = {item.score_item_name.replace(" ", ""): item for item in parsed.items}
        assert by_name["投标报价"].volume == "price"
        assert by_name["服务方案"].volume == "technical"
        assert by_name["企业业绩"].volume == "qualification"

    def test_real_group_column_wins_over_item_marker(self) -> None:
        parsed = parse_scoring_table(
            "",
            section_path="第三章 评标办法",
            grid=[
                ["", "评审因素", "分值", "评分标准"],
                ["技术标（70分）", "投标报价", "10", "按插值法计算得分。经济标评分标准"],
            ],
        )
        by_name = {item.score_item_name.replace(" ", ""): item for item in parsed.items}
        assert by_name["投标报价"].volume == "technical"

    def test_bare_item_name_does_not_invent_qualification(self) -> None:
        parsed = parse_scoring_table(
            "",
            section_path="第三章 评标办法",
            grid=[
                ["条款号", "评分因素", "标准分", "评分标准"],
                ["2.2.3", "企业业绩", "5", "近三年类似项目每项得1分，最高5分。"],
            ],
        )
        by_name = {item.score_item_name.replace(" ", ""): item for item in parsed.items}
        assert by_name["企业业绩"].volume == "technical"
        assert by_name["企业业绩"].volume_label in (None, "")

    def test_empty_group_markers_stick_until_next_marker(self) -> None:
        """3425: 短语在每段首行, 后续行继承, 新短语切换; 合计 10/70/20。"""
        parsed = parse_scoring_table(
            "",
            section_path="第三章 评标办法",
            grid=[
                ["分组", "评分因素", "评分内容与标准", "分值"],
                ["", "投标报价", "按插值法计算得分。经济标评分标准", "10"],
                ["", "服务方案", "方案完整可行得满分。技术标评分标准", "10"],
                ["", "服务质量管控", "管控措施可行得满分。", "10"],
                ["", "应急处理保障", "应急机制可行得满分。", "10"],
                ["", "维修安全制度", "安全制度完善得满分。", "10"],
                ["", "售后服务方案", "售后方案完善得满分。", "10"],
                ["", "货物质量方案", "质量方案完善得满分。", "10"],
                ["", "维修定位", "重难点定位准确得满分。", "10"],
                ["", "企业业绩", "近三年类似业绩每项得1分。资信业绩评分标准", "5"],
                ["", "拟派项目管理班子资历", "特种作业资格每人3分。", "15"],
            ],
        )
        by_name = {item.score_item_name.replace(" ", ""): item for item in parsed.items}
        assert by_name["投标报价"].volume == "price"
        assert by_name["服务方案"].volume == "technical"
        assert by_name["维修定位"].volume == "technical"
        assert by_name["企业业绩"].volume == "qualification"
        assert by_name["拟派项目管理班子资历"].volume == "qualification"
        totals: dict[str, float] = {}
        for item in parsed.items:
            totals[item.volume] = totals.get(item.volume, 0.0) + float(item.score_weight)
        assert totals == {"price": 10.0, "technical": 70.0, "qualification": 20.0}


class TestRescueExplicitBidLabel:
    def test_short_labels_map_without_volume_defs(self) -> None:
        assert volume_from_explicit_bid_label("经济标") == ("price", "经济标")
        assert volume_from_explicit_bid_label("技术标") == ("technical", "技术标")
        assert volume_from_explicit_bid_label("商务标") == ("commercial", "商务标")
        assert volume_from_explicit_bid_label("综合标") == ("composite", "综合标")
        assert volume_from_explicit_bid_label("资信标") == ("qualification", "资信标")
        assert volume_from_explicit_bid_label("技术方案") is None
        assert volume_from_explicit_bid_label("企业业绩") is None

    def test_empty_volume_defs_fills_from_group_path(self) -> None:
        items = to_extracted_items(
            [
                VlmScoringItemDraft(
                    score_item_name="投标报价",
                    score_weight=10.0,
                    group_path=["经济标"],
                ),
                VlmScoringItemDraft(
                    score_item_name="服务方案",
                    score_weight=10.0,
                    group_path=["技术标"],
                ),
                VlmScoringItemDraft(
                    score_item_name="拟派项目管理班子资历",
                    score_weight=15.0,
                    group_path=["资信标"],
                ),
            ],
            volume_defs={},
        )
        by_name = {item.score_item_name: item for item in items}
        assert by_name["投标报价"].volume == "price"
        assert by_name["服务方案"].volume == "technical"
        assert by_name["拟派项目管理班子资历"].volume == "qualification"

    def test_empty_volume_defs_does_not_default_technical(self) -> None:
        items = to_extracted_items(
            [
                VlmScoringItemDraft(
                    score_item_name="服务方案",
                    score_weight=10.0,
                    group_path=["评审因素"],
                )
            ],
            volume_defs={},
        )
        assert items[0].volume is None
