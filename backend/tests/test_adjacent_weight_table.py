"""邻近外层权重表消费防御测试。

覆盖: 3412 裸数字权重表→三卷定义; fail-closed 三闸; 明细表误触发拒绝;
 百分号权重形态仍走旧路径、新路径拒收。
不做: 不访问 DB、不调用 LLM。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.extractor.scoring.adjacent_weight_table import (
    volume_defs_from_neighbor_chunks,
)
from app.core.extractor.scoring.weighted_volume_table import (
    parse_bare_weight_volume_table,
    parse_weighted_volume_definitions,
)

pytestmark = pytest.mark.scoring


def test_3412_bare_weight_table_yields_three_volume_defs():
    header = ["序号", "评分项", "权重(%)"]
    data = [
        ["1", "价格", "30"],
        ["采用低价优先法计算，即满足招标文件要求且投标报价最低的价格为评标基准价", "", ""],
        ["2", "技术部分", "54"],
        ["3", "商务部分", "16"],
    ]
    defs = parse_bare_weight_volume_table(header, data)
    assert set(defs) == {"price", "technical", "commercial"}
    assert defs["price"].volume_max == 30.0
    assert defs["price"].weight == 0.3
    assert defs["price"].source == "framework"
    assert defs["price"].row_title == "价格"
    assert "低价优先" in (defs["price"].evidence_text or "")
    assert defs["technical"].volume_max == 54.0
    assert defs["commercial"].volume_max == 16.0
    assert abs(sum(d.volume_max for d in defs.values()) - 100.0) <= 1.0


def test_bare_weight_rejects_sum_not_100():
    header = ["序号", "评分项", "权重(%)"]
    data = [["1", "价格", "30"], ["2", "技术部分", "40"]]
    assert parse_bare_weight_volume_table(header, data) == {}


def test_bare_weight_rejects_single_volume():
    header = ["序号", "评分项", "权重(%)"]
    data = [["1", "价格", "100"]]
    assert parse_bare_weight_volume_table(header, data) == {}


def test_bare_weight_rejects_duplicate_volume():
    header = ["序号", "评分项", "权重(%)"]
    data = [
        ["1", "价格", "30"],
        ["2", "报价", "30"],
        ["3", "技术部分", "40"],
    ]
    assert parse_bare_weight_volume_table(header, data) == {}


def test_bare_weight_ignores_detail_item_rows_with_weight_column():
    header = ["序号", "评分因素", "权重"]
    data = [
        ["1", "技术参数响应", "24"],
        ["2", "项目实施计划", "6"],
        ["3", "产品质量管控", "6"],
    ]
    assert parse_bare_weight_volume_table(header, data) == {}


def test_bug601_percent_weight_still_parsed_by_legacy_path():
    header = ["", "权重", "评审因素", "分值", "评审标准"]
    data = [
        ["资信标（100分）", "10%", "体系认证", "0-30", "每提供一项证书得10分"],
        ["技术标（100分）", "50%", "项目实施方案", "0-18", "方案科学合理"],
        ["商务标（100分）", "40%", "投标报价", "0-100", "等于评标基准价得100分"],
    ]
    legacy = parse_weighted_volume_definitions(header, data)
    assert set(legacy) == {"qualification", "technical", "commercial"}
    assert legacy["qualification"].weight == 0.1
    assert all(defn.volume_max == 100.0 for defn in legacy.values())
    assert parse_bare_weight_volume_table(header, data) == {}


def test_neighbor_chunks_ignores_prose_weight_mention_before_header():
    """说明段落含「权重」不得抢占真正的列级权重表头(doc2477 实测形态)。"""
    chunks = [
        SimpleNamespace(
            chunk_index=37, page_num=5,
            content="表格第2行：评标总得分＝F1×A1＋F2×A2；An分别为各项评审因素所占的权重(A1＋A2＋……＋An＝1)。",
        ),
        SimpleNamespace(
            chunk_index=38, page_num=5,
            content="表格第3行：序号 | 评分项 | 权重(%)",
        ),
        SimpleNamespace(
            chunk_index=39, page_num=5,
            content="表格第4行：1 | 价格 | 30",
        ),
        SimpleNamespace(
            chunk_index=41, page_num=5,
            content="表格第6行：2 | 技术部分 | 54",
        ),
        SimpleNamespace(
            chunk_index=54, page_num=8,
            content="表格第3行：3 | 商务部分 | 16",
        ),
    ]
    defs = volume_defs_from_neighbor_chunks(chunks)
    assert set(defs) == {"price", "technical", "commercial"}
    assert defs["price"].volume_max == 30.0
    assert defs["commercial"].volume_max == 16.0


def test_neighbor_chunks_3412_interrupted_by_detail_header():
    """商务部分被嵌套明细表头打断后仍应并入同一权重表。"""
    chunks = [
        SimpleNamespace(
            chunk_index=38, page_num=5,
            content="表格第3行：序号 | 评分项 | 权重(%)",
        ),
        SimpleNamespace(
            chunk_index=39, page_num=5,
            content="表格第4行：1 | 价格 | 30",
        ),
        SimpleNamespace(
            chunk_index=40, page_num=5,
            content="表格第5行：采用低价优先法计算，投标报价得分=（评标基准价／投标报价）×价格权值×100",
        ),
        SimpleNamespace(
            chunk_index=41, page_num=5,
            content="表格第6行：2 | 技术部分 | 54",
        ),
        SimpleNamespace(
            chunk_index=42, page_num=5,
            content="表格第7行：序号 | 评分因素 | 分值 | 评分准则",
        ),
        SimpleNamespace(
            chunk_index=43, page_num=5,
            content="表格第8行：1 | 技术参数响应 | 24 | 带星号条款为不可偏离",
        ),
        SimpleNamespace(
            chunk_index=80, page_num=8,
            content="表格第1行：3 | 商务部分 | 16",
        ),
    ]
    defs = volume_defs_from_neighbor_chunks(chunks)
    assert set(defs) == {"price", "technical", "commercial"}
    assert defs["price"].volume_max == 30.0
    assert defs["commercial"].volume_max == 16.0
    assert "价格权值" in (defs["price"].evidence_text or "")
