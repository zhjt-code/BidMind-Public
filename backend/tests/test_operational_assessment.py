""": 履约/运营考核表软标记——仍提取，确认页标「疑似履约考核，建议排除」。

职责: 锁定食堂考核表等非评标表的文本签名、页重叠归属、核计排除与落库标记。
不做: 不改 硬拒绝（考核项目+扣分事由/考核人仍不进候选）。
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")

import pytest

from app.core.extractor.scoring.operational_assessment import (  # noqa: E402
    assessment_pages_from_chunks,
    item_is_suspected_assessment,
    suspected_assessment_notice,
)
from app.core.extractor.scoring.scoring_item_metadata import (  # noqa: E402
    build_scoring_item_metadata,
)
from app.core.parser.scoring_table_detector import (  # noqa: E402
    analyze_scoring_table_rows,
    looks_like_operational_assessment,
)
from app.schemas.requirement import ExtractedScoringItem  # noqa: E402
from app.services.writing.scoring_report import build_scoring_report_payload  # noqa: E402

pytestmark = pytest.mark.scoring

# 项目3422 doc2492 第55-56页真实形态（月度食堂服务质量考核表）
_CANTEEN_CHUNK = (
    "[评分表 第55-56页]\n"
    "| 月度食堂服务质量考核表考核时间：  年  月  日"
    "考核周期：  年  月  考核类型：□日常检查 □突击检查"
    "考核方（甲方）：大兴区林校路街道办事处 被考核方（乙方）： |\n"
    "| 考核项目 | 分值 | 评分标准 |\n"
    "| 食品安全管理 | 25分 | 健康证缺一人或过期一人扣5分，当月直接判定不合格 |\n"
)

_BID_EVAL_CHUNK = (
    "[评分表 第41-43页]\n"
    "| 序号 | 评分因素 | 分值 | 评分标准 |\n"
    "| 1 | 类似业绩 | 15分 | 投标人近三年内承接的类似项目业绩，每提供1个有效业绩得3分 |\n"
)


def test_canteen_assessment_table_is_flagged_not_hard_rejected():
    """3422 食堂考核表应软标记；因无扣分事由/考核人，不得走 硬拒绝。"""
    rows = [
        ["月度食堂服务质量考核表", "考核时间", "考核周期"],
        ["考核方（甲方）", "被考核方（乙方）", ""],
        ["考核项目", "分值", "评分标准"],
        ["食品安全管理", "25", "健康证缺一人扣5分，当月直接判定不合格"],
    ]
    analysis = analyze_scoring_table_rows(rows)
    assert analysis.is_scoring_candidate is True
    assert "assessment_form" not in analysis.reasons
    assert looks_like_operational_assessment(_CANTEEN_CHUNK) is True


def test_bid_eval_table_with_assessment_word_in_rules_is_not_flagged():
    """护栏: 评标表规则出现「考核得分」且含投标人锚点时不得软标记。"""
    assert looks_like_operational_assessment(_BID_EVAL_CHUNK) is False
    rows = [
        ["项目", "分值", "评分标准"],
        ["履约评价", "3", "根据考核得分由高到低进行排名，排名前30%得3分。"],
    ]
    assert looks_like_operational_assessment(
        "\n".join("|".join(r) for r in rows)
    ) is False


def test_bug556_hard_reject_still_applies():
    """ 售后考核表硬拒绝不得被软标记路径取代。"""
    rows = [
        ["号", "序 考 核项 目", "项 目 总 分", "单 项 细 分", "单 项 分 数", "单 项 得 分", "考核项目说明", "扣 分 事 由"],
        ["", "1 故障修复基础管理", "70", "故障响应时限", "10", "", "接到故障申告0.5小时内响应", ""],
        ["总分", "", "", "", "100", "", "考核人：", ""],
    ]
    analysis = analyze_scoring_table_rows(rows)
    assert analysis.is_scoring_candidate is False
    assert "assessment_form" in analysis.reasons


def test_item_flagged_only_when_pages_subset_of_assessment_chunks():
    canteen = SimpleNamespace(content=_CANTEEN_CHUNK, page_num=55)
    bid = SimpleNamespace(content=_BID_EVAL_CHUNK, page_num=41)
    pages = assessment_pages_from_chunks([canteen, bid])
    assert 55 in pages and 56 in pages
    assert 41 not in pages

    flagged = SimpleNamespace(source_pages=[55, 56], source_page=55)
    real = SimpleNamespace(source_pages=[41, 42], source_page=41)
    assert item_is_suspected_assessment(flagged, pages) is True
    assert item_is_suspected_assessment(real, pages) is False


def test_metadata_only_writes_flag_when_true():
    item = ExtractedScoringItem(
        category="TECH",
        score_item_name="食品安全管理",
        description="健康证缺一人扣5分",
        score_weight=25.0,
    )
    plain = build_scoring_item_metadata(
        item, effective_weight=25.0, totals_needs_review=False
    )
    assert "suspected_assessment_form" not in plain

    flagged = build_scoring_item_metadata(
        item,
        effective_weight=25.0,
        totals_needs_review=False,
        suspected_assessment_form=True,
    )
    assert flagged["suspected_assessment_form"] is True


def test_writing_report_excludes_suspected_assessment_from_totals():
    """评标项闭合100时，食堂考核加分不得把核计打红；需带建议排除计数。"""
    clauses = [
        SimpleNamespace(
            sub_type="scoring_criterion",
            score_weight=60.0,
            metadata_json={"source": "scoring_table", "volume": "technical"},
            source_page=41,
        ),
        SimpleNamespace(
            sub_type="scoring_criterion",
            score_weight=40.0,
            metadata_json={"source": "scoring_table", "volume": "technical"},
            source_page=41,
        ),
        SimpleNamespace(
            sub_type="scoring_criterion",
            score_weight=25.0,
            metadata_json={
                "source": "scoring_table",
                "suspected_assessment_form": True,
            },
            source_page=55,
        ),
        SimpleNamespace(
            sub_type="scoring_criterion",
            score_weight=30.0,
            metadata_json={
                "source": "scoring_table",
                "suspected_assessment_form": True,
            },
            source_page=55,
        ),
    ]
    payload = build_scoring_report_payload(clauses)
    assert payload is not None
    assert payload["item_count"] == 2
    assert payload["needs_review"] is False
    assert payload["suspected_assessment_count"] == 2
    assert payload["suspected_assessment_score"] == 55.0
    assert "疑似履约考核" in suspected_assessment_notice(2, 55.0)
