# -*- coding: utf-8 -*-
"""SchemeMap 归属贴标测试（SchemeReader 阶段2）。

防御目标(映射真实风险):
- (2249形态): 同一张表内卷头行切分的两段, 贴标必须能把"商务评分"段
 的条目从错误的 technical 改到 commercial。
- 误贴防线: 因素文本不匹配时禁止仅凭分值贴标; 候选归属跨卷歧义时放弃(fail-open)。
- shadow 红线: enforce=False 时禁止改动任何条目字段。
"""

import pytest

from app.core.extractor.scoring.scheme_reader.attribution import (
    stamp_scheme_attribution,
)
from app.core.extractor.scoring.scheme_reader.contracts import SchemeMap
from app.schemas.requirement import ExtractedScoringItem

pytestmark = pytest.mark.scoring


def _item(name, score, volume="technical", scheme="scheme_1", pages=None):
    return ExtractedScoringItem(
        category="TECH",
        score_item_name=name,
        description=f"{name}的评分标准描述",
        score_weight=score,
        volume=volume,
        scheme=scheme,
        source_pages=pages or [20],
    )


# 形态: 单表内卷头切分 ----------

SIGNALS_2249 = {
    "version": 1,
    "sections": [{"title": "第四章 评标办法", "page_start": 20, "page_end": 20}],
    "declarations": [
        {"page": 20, "text": "分值构成：技术标53分，商务标32分，价格标15分。", "kind": "keyword"}
    ],
    "headings": [],
    "tables": [
        {
            "page": 20,
            "table_index": 0,
            "row_count": 8,
            "col_count": 3,
            "header_rows": [{"row_index": 0, "cells": ["评分因素", "评分标准", "分值"]}],
            "total_rows": [],
            "rows": [
                {"row_index": 1, "factor": "技术评分（总分53分）", "score": ""},
                {"row_index": 2, "factor": "技术方案", "score": "33"},
                {"row_index": 3, "factor": "实施计划", "score": "20"},
                {"row_index": 4, "factor": "商务评分（总分32分）", "score": ""},
                {"row_index": 5, "factor": "综合能力", "score": "12"},
                {"row_index": 6, "factor": "企业业绩", "score": "20"},
                {"row_index": 7, "factor": "价格评分（总分15分）", "score": "15"},
            ],
        }
    ],
    "pages": [{"page": 20, "text_sparse": False}],
}

SCHEME_MAP_2249 = SchemeMap(
    **{
        "schemes": [
            {
                "scheme_index": 1,
                "title": "第四章 评标办法",
                "total_score": 100,
                "volumes": [
                    {
                        "volume": "technical", "label": "技术标", "max_score": 53,
                        "anchor": {"kind": "declaration", "page": 20, "quote": "技术标53分"},
                    },
                    {
                        "volume": "commercial", "label": "商务标", "max_score": 32,
                        "anchor": {"kind": "declaration", "page": 20, "quote": "商务标32分"},
                    },
                    {
                        "volume": "price", "label": "价格标", "max_score": 15,
                        "anchor": {"kind": "declaration", "page": 20, "quote": "价格标15分"},
                    },
                ],
            }
        ],
        "assignments": [
            {
                "page": 20, "table_index": 0, "row_start": 1, "row_end": 3,
                "scheme_index": 1, "volume": "technical", "volume_label": "技术标",
            },
            {
                "page": 20, "table_index": 0, "row_start": 4, "row_end": 6,
                "scheme_index": 1, "volume": "commercial", "volume_label": "商务标",
            },
            {
                "page": 20, "table_index": 0, "row_start": 7, "row_end": 7,
                "scheme_index": 1, "volume": "price", "volume_label": "价格标",
            },
        ],
        "confidence": 0.9,
    }
)


def _items_2249():
    """现行管线产物形态: 商务段条目被"最近卷头继承"错标为 technical。"""
    return [
        _item("技术方案", 33, volume="technical"),
        _item("实施计划", 20, volume="technical"),
        _item("综合能力", 12, volume="technical"),   # 错标
        _item("企业业绩", 20, volume="technical"),   # 错标
        _item("价格评分", 15, volume="price"),
    ]


def test_enforce_restamps_misattributed_commercial_items():
    """ 核心: enforce 模式必须把商务段条目改回 commercial。"""
    items = _items_2249()
    outcome = stamp_scheme_attribution(
        items, SCHEME_MAP_2249, SIGNALS_2249, enforce=True
    )

    assert outcome.stamped == 2
    by_name = {i.score_item_name: i for i in items}
    assert by_name["综合能力"].volume == "commercial"
    assert by_name["综合能力"].volume_label == "商务标"
    assert by_name["企业业绩"].volume == "commercial"
    # 本就正确的条目不动
    assert by_name["技术方案"].volume == "technical"
    assert by_name["价格评分"].volume == "price"


def test_shadow_records_divergence_without_mutation():
    """shadow 红线: 只记分歧, 禁止改动条目。"""
    items = _items_2249()
    outcome = stamp_scheme_attribution(
        items, SCHEME_MAP_2249, SIGNALS_2249, enforce=False
    )

    assert outcome.stamped == 0
    assert any("综合能力" in d for d in outcome.divergences)
    assert all(i.volume in ("technical", "price") for i in items)


def test_no_score_only_match_when_factor_text_disagrees():
    """误贴防线: 因素文本不匹配时禁止仅凭同分值贴标。"""
    items = [_item("完全无关的评分项", 12, volume="technical")]
    outcome = stamp_scheme_attribution(
        items, SCHEME_MAP_2249, SIGNALS_2249, enforce=True
    )
    assert outcome.stamped == 0
    assert items[0].volume == "technical"


def test_ambiguous_cross_volume_match_is_skipped():
    """歧义防线: 同名因素出现在不同卷的区间时放弃贴标。"""
    signals = {
        "version": 1,
        "sections": [],
        "declarations": [],
        "headings": [],
        "tables": [
            {
                "page": 10, "table_index": 0, "row_count": 4, "col_count": 3,
                "header_rows": [], "total_rows": [],
                "rows": [
                    {"row_index": 1, "factor": "售后服务", "score": "10"},
                    {"row_index": 2, "factor": "售后服务", "score": "10"},
                ],
            }
        ],
        "pages": [],
    }
    scheme_map = SchemeMap(
        **{
            "schemes": [
                {"scheme_index": 1, "volumes": [{"volume": "technical"}]},
                {"scheme_index": 2, "volumes": [{"volume": "commercial"}]},
            ],
            "assignments": [
                {
                    "page": 10, "table_index": 0, "row_start": 1, "row_end": 1,
                    "scheme_index": 1, "volume": "technical",
                },
                {
                    "page": 10, "table_index": 0, "row_start": 2, "row_end": 2,
                    "scheme_index": 2, "volume": "commercial",
                },
            ],
            "confidence": 0.9,
        }
    )
    items = [_item("售后服务", 10, pages=[10])]
    outcome = stamp_scheme_attribution(items, scheme_map, signals, enforce=True)

    assert outcome.stamped == 0
    assert outcome.unmatched == 1
    assert any("歧义" in d for d in outcome.divergences)


def test_digit_factor_matches_by_page_and_score():
    """纯序号因素行: 允许 页码∩分值 双证据匹配(2250 骨架的常见形态)。"""
    signals = {
        "version": 1, "sections": [], "declarations": [], "headings": [],
        "tables": [
            {
                "page": 11, "table_index": 0, "row_count": 3, "col_count": 4,
                "header_rows": [], "total_rows": [],
                "rows": [{"row_index": 1, "factor": "1", "score": "40"}],
            }
        ],
        "pages": [],
    }
    scheme_map = SchemeMap(
        **{
            "schemes": [{"scheme_index": 2, "volumes": [{"volume": "commercial"}]}],
            "assignments": [
                {
                    "page": 11, "table_index": 0, "row_start": 1, "row_end": 1,
                    "scheme_index": 2, "volume": "commercial", "volume_label": "商务评审",
                }
            ],
            "confidence": 0.9,
        }
    )
    items = [_item("企业资质", 40, volume="technical", pages=[11])]
    outcome = stamp_scheme_attribution(items, scheme_map, signals, enforce=True)

    assert outcome.stamped == 1
    assert items[0].volume == "commercial"
    assert items[0].scheme == "scheme_2"


def test_empty_records_skips_gracefully():
    outcome = stamp_scheme_attribution(
        [_item("技术方案", 33)],
        SchemeMap(confidence=0.9),
        {"tables": []},
        enforce=True,
    )
    assert outcome.stamped == 0
    assert any("跳过" in d for d in outcome.divergences)
