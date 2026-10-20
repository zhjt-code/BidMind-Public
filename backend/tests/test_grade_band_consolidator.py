# -*- coding: utf-8 -*-
"""档次带归并测试。

防御目标(映射真实风险):
- 2305形态: "主行3分+档次行2.4/2.1"三行重复计分111/100 → 归并后守恒;
- 误伤防线: 非派生名小分项、比值超区间、跨页条目一律不归并;
- 开关红线: 关闭后零改动。
"""

from unittest.mock import patch

import pytest

from app.core.config import settings
from app.core.extractor.scoring.grade_band_consolidator import (
    consolidate_grade_bands,
    consolidate_grade_bands_if_enabled,
)
from app.schemas.requirement import ExtractedScoringItem

pytestmark = pytest.mark.scoring


def _item(name, score, pages=None):
    return ExtractedScoringItem(
        category="TECH",
        score_item_name=name,
        description=f"{name}的评分描述内容",
        score_weight=score,
        source_pages=pages or [3],
    )


def _items_2305_shape():
    """2305真实形态: 主行(满分)后跟80%/70%档次行(条款号派生名)。"""
    return [
        _item("项目管理大纲编制（5分）", 5.0),
        _item("2.2.4（1）", 4.0),
        _item("2.2.4（1）", 3.5),
        _item("对本工程的认识和理解（3分）", 3.0),
        _item("2.2.4（2）", 2.4),
        _item("2.2.4（2）", 2.1),
        _item("偏差率", 60.0),
    ]


def test_consolidates_2305_grade_bands_and_conserves_totals():
    """ 核心: 档次行归并后总分从盈余回到主行合计。"""
    items, diags = consolidate_grade_bands(_items_2305_shape())

    names = [i.score_item_name for i in items]
    assert names == ["项目管理大纲编制（5分）", "对本工程的认识和理解（3分）", "偏差率"]
    assert sum(i.score_weight for i in items) == 68.0  # 5+3+60, 不再是 80.0
    # 档次描述并入主行规则, 信息不丢失
    main = items[0]
    assert any("4分" in r for r in main.score_rules)
    assert any("3.5分" in r for r in main.score_rules)
    assert diags and "归并" in diags[0]


def test_does_not_merge_named_small_items():
    """误伤防线: 正常命名的小分项(如售后服务2分)不是档次行。"""
    items, diags = consolidate_grade_bands([
        _item("技术方案", 5.0),
        _item("售后服务", 4.0),
        _item("培训方案", 3.0),
    ])
    assert len(items) == 3
    assert diags == []


def test_does_not_merge_when_ratio_out_of_band():
    """误伤防线: 派生名但比值超出折扣带区间(独立小分项/满分复现)不归并。"""
    items, _ = consolidate_grade_bands([
        _item("企业业绩（10分）", 10.0),
        _item("2.2.4（1）", 2.0),    # 0.2 < 0.5
        _item("2.2.4（2）", 10.0),   # >= 主行分值
    ])
    assert len(items) == 3


def test_does_not_merge_across_pages():
    """误伤防线: 跨页派生名条目不归并(不同表的条款号行)。"""
    items, _ = consolidate_grade_bands([
        _item("技术方案（5分）", 5.0, pages=[3]),
        _item("2.2.4（1）", 4.0, pages=[9]),
    ])
    assert len(items) == 2


def test_derived_name_without_main_is_kept():
    """无主行时档次样行原样保留(fail-open, 不凭空丢数据)。"""
    items, _ = consolidate_grade_bands([
        _item("2.2.4（1）", 4.0),
        _item("技术方案", 5.0),
    ])
    assert len(items) == 2


def test_switch_off_restores_original_behavior():
    source = _items_2305_shape()
    with patch.object(settings, "SCORING_GRADE_BAND_CONSOLIDATION_ENABLED", False):
        items, diags = consolidate_grade_bands_if_enabled(source)
    assert items is source
    assert diags == []


# 阶段3(2026-08-31): tail 接线用例(test_tail_consolidates_before_validation)随
# legacy 尾部执行器删除; 归并算法本体用例保留如上。
