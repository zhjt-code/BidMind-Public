"""文档级评分表存在性门禁防御测试。

职责: 锁定三态判定、谈判先验、锚点作用域与考核表不计入候选。
不做: 不调真实 LLM/DB、不跑 pdf_direct 抽取。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")

import pytest

from app.core.extractor.scoring.scoring_table_presence import (  # noqa: E402
    decide_scoring_table_presence,
)

pytestmark = pytest.mark.scoring

_BID_GRID = [
    ["序号", "评分因素", "分值", "评分标准"],
    ["1", "类似业绩", "15", "投标人近三年内承接的类似项目业绩，每提供1个有效业绩得3分"],
]
_KPI_GRID = [
    ["技术参数", "单位", "要求值"],
    ["响应时间", "秒", "3"],
    ["容量", "人", "200"],
]
_CANTEEN_GRID = [
    ["月度食堂服务质量考核表", "考核时间", "考核周期"],
    ["考核方（甲方）", "被考核方（乙方）", ""],
    ["考核项目", "分值", "评分标准"],
    ["食品安全管理", "25", "健康证缺一人扣5分，当月直接判定不合格"],
]
_CANTEEN_CONTENT = (
    "[评分表 第55-56页]\n"
    "月度食堂服务质量考核表 考核周期 考核方（甲方） 被考核方（乙方）\n"
    "| 考核项目 | 分值 | 评分标准 |\n"
    "| 食品安全管理 | 25分 | 健康证缺一人扣5分 |\n"
)
_BID_CONTENT = (
    "[评分表 第2-3页]\n"
    "| 序号 | 评分因素 | 分值 | 评分标准 |\n"
    "| 1 | 类似业绩 | 15分 | 投标人近三年内承接的类似项目业绩 |\n"
)


def test_negotiation_kpi_without_anchor_is_absent():
    decision = decide_scoring_table_presence(
        {1: "竞争性谈判采购公告", 2: "技术指标与考核指标一览"},
        [_KPI_GRID],
        [{2}],
        ["[评分表 第2页]\n| 技术参数 | 单位 | 要求值 |\n| 响应时间 | 秒 | 3 |"],
    )
    assert decision.verdict == "absent"
    assert decision.prior == "negotiation_like"
    assert decision.blocks_extraction is True


def test_open_tender_spec_table_without_anchor_is_absent():
    decision = decide_scoring_table_presence(
        {1: "公开招标公告", 2: "采购需求 技术规格"},
        [_KPI_GRID],
        [{2}],
        ["[评分表 第2页]\n| 技术参数 | 单位 | 要求值 |"],
    )
    assert decision.verdict == "absent"
    assert decision.prior == "competitive"
    assert decision.blocks_extraction is True


def test_open_tender_scoring_table_with_anchor_is_present():
    decision = decide_scoring_table_presence(
        {1: "公开招标公告", 2: "评标办法 投标人资格后进入详细评审"},
        [_BID_GRID],
        [{2}],
        [_BID_CONTENT],
    )
    assert decision.verdict == "present"
    assert 2 in decision.candidate_pages


def test_negotiation_with_real_scoring_table_is_present():
    decision = decide_scoring_table_presence(
        {1: "竞争性谈判公告", 2: "评标办法 投标人须按评分因素响应"},
        [_BID_GRID],
        [{2}],
        [_BID_CONTENT],
    )
    assert decision.verdict == "present"
    assert decision.prior == "negotiation_like"
    assert decision.blocks_extraction is False


def test_mixed_scoring_and_canteen_keeps_present():
    decision = decide_scoring_table_presence(
        {1: "公开招标", 2: "评标办法 投标人", 55: "食堂考核表"},
        [_BID_GRID, _CANTEEN_GRID],
        [{2}, {55, 56}],
        [_BID_CONTENT, _CANTEEN_CONTENT],
    )
    assert decision.verdict == "present"
    assert 2 in decision.candidate_pages
    assert 55 not in decision.candidate_pages
    assert 56 not in decision.candidate_pages


def test_empty_text_layer_is_uncertain():
    decision = decide_scoring_table_presence({1: "", 2: "   "}, [], [], [], title="")
    assert decision.verdict == "uncertain"
    assert "no_text_layer" in decision.reasons


def test_located_pages_with_anchor_without_candidate_is_uncertain():
    decision = decide_scoring_table_presence(
        {3: "评标办法 评分 分值 第3分册 投标人资格审查后续页"},
        [],
        [],
        [],
    )
    assert decision.verdict == "uncertain"
    assert "located_pages" in decision.reasons
    assert decision.blocks_extraction is False


def test_document_wide_anchor_outside_scope_is_not_present():
    decision = decide_scoring_table_presence(
        {1: "投标人须知 公开招标", 10: "评分因素 分值 评分标准 类似业绩每项得3分"},
        [_BID_GRID],
        [{10}],
        ["[评分表 第10页]\n| 评分因素 | 分值 | 评分标准 |"],
    )
    assert decision.verdict != "present"
    assert decision.verdict == "uncertain"


def test_consultation_is_not_negotiation_prior():
    decision = decide_scoring_table_presence(
        {1: "竞争性磋商公告", 2: "采购需求"},
        [_KPI_GRID],
        [{2}],
        ["指标表"],
    )
    assert decision.prior == "competitive"
    assert decision.verdict == "absent"
