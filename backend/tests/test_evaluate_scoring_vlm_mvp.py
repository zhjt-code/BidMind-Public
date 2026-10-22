"""VLM评分MVP评估脚本纯函数防御测试。

覆盖: JSON提取(围栏/思考段/截断)、宽松数值纠偏、事实层同口径后处理
 (0分base_score过滤 + 命名净化复用)、总分口径。
不做: 不调用LLM、不渲染PDF、不访问DB。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from scripts.evaluate_scoring_vlm_mvp import (  # noqa: E402
    _coerce_float,
    effective_items,
    extract_json_block,
    extract_jsonl_records,
    vlm_total_score,
)

pytestmark = pytest.mark.scoring


class TestExtractJsonBlock:
    def test_plain_json(self):
        assert extract_json_block('{"items": []}') == {"items": []}

    def test_json_in_code_fence(self):
        text = '好的，以下是结果:\n```json\n{"declared_total": 100}\n```\n'
        assert extract_json_block(text) == {"declared_total": 100}

    def test_think_segment_stripped(self):
        text = '<think>我先看第一列{未闭合</think>{"items": [{"a": 1}]}'
        assert extract_json_block(text) == {"items": [{"a": 1}]}

    def test_truncated_json_returns_none(self):
        text = '{"items": [{"score_item_name": "技术'
        assert extract_json_block(text) is None

    def test_brace_inside_string_not_counted(self):
        text = '{"name": "含}括号的名称", "v": 1}'
        assert extract_json_block(text) == {"name": "含}括号的名称", "v": 1}

    def test_leading_prose_then_json(self):
        text = '提取结果如下: {"items": []} 以上。'
        assert extract_json_block(text) == {"items": []}


class TestExtractJsonlRecords:
    def test_header_and_items(self):
        text = (
            '{"type": "header", "declared_total": 100, "volume_totals": []}\n'
            '{"type": "item", "score_item_name": "技术方案", "score_weight": 20}\n'
            '{"type": "item", "score_item_name": "业绩", "score_weight": 10}\n'
        )
        header, items, broken = extract_jsonl_records(text)
        assert header["declared_total"] == 100
        assert [i["score_item_name"] for i in items] == ["技术方案", "业绩"]
        assert broken == 0

    def test_truncated_tail_line_only_loses_tail(self):
        text = (
            '{"type": "header", "declared_total": 100}\n'
            '{"type": "item", "score_item_name": "技术方案", "score_weight": 20}\n'
            '{"type": "item", "score_item_name": "被截断的项", "score_wei'
        )
        header, items, broken = extract_jsonl_records(text)
        assert header is not None
        assert len(items) == 1
        assert broken == 1

    def test_code_fence_and_trailing_comma_tolerated(self):
        text = (
            "```json\n"
            '{"type": "item", "score_item_name": "业绩", "score_weight": 10},\n'
            "```"
        )
        _, items, broken = extract_jsonl_records(text)
        assert len(items) == 1
        assert broken == 0

    def test_header_without_type_field_recognized(self):
        text = '{"declared_total": 100, "volume_totals": [{"label": "技术卷", "total": 40}]}'
        header, items, _ = extract_jsonl_records(text)
        assert header is not None
        assert items == []

    def test_empty_response_returns_nothing(self):
        header, items, broken = extract_jsonl_records("抱歉, 图片中没有评分表。")
        assert header is None
        assert items == []
        assert broken == 0


class TestCoerceFloat:
    def test_plain_number(self):
        assert _coerce_float(10) == 10.0
        assert _coerce_float(2.5) == 2.5

    def test_score_with_unit(self):
        assert _coerce_float("10分") == 10.0

    def test_range_takes_last(self):
        assert _coerce_float("0-5") == 5.0

    def test_unparseable_returns_none(self):
        assert _coerce_float("未声明") is None
        assert _coerce_float(None) is None

    def test_bool_rejected(self):
        assert _coerce_float(True) is None


class TestEffectiveItems:
    def test_zero_score_base_row_filtered(self):
        items = [
            {"score_item_name": "应答函", "score_weight": 0.0, "fact_type": "base_score"},
            {"score_item_name": "技术方案", "score_weight": 20.0, "fact_type": "base_score"},
        ]
        kept = effective_items(items)
        assert [i["score_item_name"] for i in kept] == ["技术方案"]

    def test_penalty_zero_score_kept(self):
        items = [
            {"score_item_name": "负面行为", "score_weight": 0.0, "fact_type": "penalty"},
        ]
        assert len(effective_items(items)) == 1

    def test_name_decoration_stripped_via_bug734_authority(self):
        items = [
            {"score_item_name": "实力及信誉（5分）", "score_weight": 5.0, "fact_type": "base_score"},
            {"score_item_name": "1、总监理工程师资质", "score_weight": 3.0, "fact_type": "base_score"},
        ]
        names = [i["score_item_name"] for i in effective_items(items)]
        assert names == ["实力及信誉", "总监理工程师资质"]

    def test_original_dict_not_mutated(self):
        item = {"score_item_name": "业绩（7分）", "score_weight": 7.0, "fact_type": "base_score"}
        effective_items([item])
        assert item["score_item_name"] == "业绩（7分）"

    def test_parent_group_row_dropped_via_group_path(self):
        items = [
            {"score_item_name": "项目管理大纲部分", "score_weight": 30.0,
             "fact_type": "base_score", "group_path": ["资信标"]},
            {"score_item_name": "项目管理大纲编制", "score_weight": 5.0,
             "fact_type": "base_score", "group_path": ["资信标", "项目管理大纲部分"]},
            {"score_item_name": "项目质量管理", "score_weight": 3.0,
             "fact_type": "base_score", "group_path": ["资信标", "项目管理大纲部分（30分)"]},
        ]
        names = [i["score_item_name"] for i in effective_items(items)]
        assert "项目管理大纲部分" not in names
        assert len(names) == 2

    def test_standard_row_duplicate_dropped(self):
        items = [
            {"score_item_name": "投标报价", "score_weight": 60.0,
             "fact_type": "price_formula", "group_path": []},
            {"score_item_name": "投标报价评分标准", "score_weight": 60.0,
             "fact_type": "base_score", "group_path": []},
        ]
        names = [i["score_item_name"] for i in effective_items(items)]
        assert names == ["投标报价"]

    def test_self_reference_in_group_path_not_suicidal(self):
        # 模型把项自身名称写进自己的group_path末位(样本12实测), 不得因此误删
        items = [
            {"score_item_name": "类似业绩", "score_weight": 1.0,
             "fact_type": "base_score", "group_path": ["技术商务部分", "类似业绩"]},
            {"score_item_name": "技术性能", "score_weight": 3.0,
             "fact_type": "base_score", "group_path": ["技术商务部分", "技术性能"]},
        ]
        assert len(effective_items(items)) == 2

    def test_enumerated_sibling_names_not_dropped(self):
        # "业绩1"以"业绩"为前缀但无标记词, 不得误删
        items = [
            {"score_item_name": "业绩", "score_weight": 3.0,
             "fact_type": "base_score", "group_path": []},
            {"score_item_name": "业绩1", "score_weight": 3.0,
             "fact_type": "base_score", "group_path": []},
        ]
        assert len(effective_items(items)) == 2

    def test_exact_cross_page_duplicate_dropped(self):
        # 跨页重复表头行(形态): 同名同分同路径只留一条
        item = {"score_item_name": "其他因素-履约信誉", "score_weight": 15.0,
                "fact_type": "base_score", "group_path": ["其他因素"]}
        assert len(effective_items([dict(item), dict(item)])) == 1

    def test_multi_lot_same_item_not_deduped(self):
        # 多包同名同分项路径不同, 是合法的独立事实, 不得合并
        items = [
            {"score_item_name": "类似业绩", "score_weight": 3.0,
             "fact_type": "base_score", "group_path": ["包1", "商务部分"]},
            {"score_item_name": "类似业绩", "score_weight": 3.0,
             "fact_type": "base_score", "group_path": ["包2", "商务部分"]},
        ]
        assert len(effective_items(items)) == 2

    def test_composition_table_copy_dropped_when_detail_exists(self):
        # 构成表(汇总层)与明细表同名同分双计: 删构成表副本, 留明细行
        items = [
            {"score_item_name": "施工组织设计", "score_weight": 35.0,
             "fact_type": "base_score",
             "group_path": ["第一个信封评分分值构成（总分100.00分）"]},
            {"score_item_name": "施工组织设计", "score_weight": 35.0,
             "fact_type": "base_score", "group_path": ["详细评审"]},
        ]
        kept = effective_items(items)
        assert len(kept) == 1
        assert kept[0]["group_path"] == ["详细评审"]

    def test_composition_table_row_kept_without_detail(self):
        # 构成表行无对应明细时是唯一事实载体, 必须保留(缺卷门禁语义)
        items = [
            {"score_item_name": "价格", "score_weight": 30.0,
             "fact_type": "price_formula",
             "group_path": ["评分分值构成"]},
        ]
        assert len(effective_items(items)) == 1


class TestVlmTotalScore:
    def test_adjustments_excluded_from_base_total(self):
        items = [
            {"score_weight": 40.0, "fact_type": "base_score"},
            {"score_weight": 60.0, "fact_type": "price_formula"},
            {"score_weight": 10.0, "fact_type": "bonus"},
            {"score_weight": 0.0, "fact_type": "penalty"},
        ]
        assert vlm_total_score(items) == 100.0
