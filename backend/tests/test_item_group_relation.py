"""评分救援候选与原文分组关系裁判防御测试。

职责: 锁定有序 table_row_with_header 证据的逐组闭合恢复与歧义降级。
不做: 不访问数据库、不调用LLM、不测试评分叶子提取。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.scoring

from types import SimpleNamespace

from app.core.extractor.scoring.item_group_relation import (
    reconcile_item_group_relations,
    volume_defs_from_group_totals,
)
from app.core.extractor.scoring.vlm.candidate_contract import (
    VlmScoringItemDraft,
    to_extracted_items,
)


def _row(index: int, page: int, content: str) -> SimpleNamespace:
    return SimpleNamespace(chunk_index=index, page_num=page, content=content)


def _doc2511_rows(*, include_technical_total: bool = True) -> list[SimpleNamespace]:
    rows = [
        _row(49, 20, "表格第2行：1 | 价格分 30分 | 报价得分计算规则"),
        _row(
            50,
            20,
            "表格第3行：2 | 综合实力和业绩经验 25分 | "
            "产品制造商综合实力 5分 | 优良4-5分",
        ),
        _row(51, 20, "表格第4行：业绩案例 20分 | 每个案例按规则计分"),
        _row(
            52,
            20,
            "表格第5行：3 | 技术指标及测试情况 | "
            "产品技术参数 15分 | 全部满足得15分，如有",
        ),
    ]
    if include_technical_total:
        rows.append(_row(54, 21, "表格第1行：30分 | 负偏离处理及扣分规则"))
    rows.extend(
        [
            _row(55, 21, "表格第2行：POC测试 15分 | 优良10-15分"),
            _row(
                56,
                21,
                "表格第3行：4 | 项目实施及维保服 10分 | "
                "实施方案（5分） | 优良4-5分",
            ),
            _row(57, 21, "表格第4行：维保服务 5分 | 优良4-5分"),
            _row(58, 21, "表格第5行：5 | 增值服务 5分 | 优良4-5分"),
        ]
    )
    return rows


def _doc2511_items() -> list:
    drafts = [
        VlmScoringItemDraft(
            score_item_name="价格分",
            score_weight=30,
            group_path=["技术卷"],
        ),
        VlmScoringItemDraft(
            score_item_name="产品制造商综合实力",
            score_weight=5,
            group_path=["综合实力和业绩经验"],
        ),
        VlmScoringItemDraft(
            score_item_name="业绩案例",
            score_weight=20,
            group_path=["综合实力和业绩经验"],
        ),
        VlmScoringItemDraft(
            score_item_name="产品技术参数",
            score_weight=15,
            group_path=["技术指标及测试情况"],
        ),
        VlmScoringItemDraft(
            score_item_name="POC测试",
            score_weight=15,
            group_path=["项目实施及维保服"],
        ),
        VlmScoringItemDraft(
            score_item_name="实施方案",
            score_weight=5,
            group_path=["项目实施及维保服"],
        ),
        VlmScoringItemDraft(
            score_item_name="维保服务",
            score_weight=5,
            group_path=["项目实施及维保服"],
        ),
        VlmScoringItemDraft(
            score_item_name="增值服务",
            score_weight=5,
            group_path=["技术卷"],
        ),
    ]
    return to_extracted_items(drafts)


def test_doc2511_cross_page_rows_recover_all_groups() -> None:
    outcome = reconcile_item_group_relations(
        _doc2511_items(),
        _doc2511_rows(),
        require_authority=True,
    )

    assert outcome.authoritative is True
    by_name = {item.score_item_name: item.volume_label for item in outcome.items}
    assert by_name == {
        "价格分": "价格分",
        "产品制造商综合实力": "综合实力和业绩经验",
        "业绩案例": "综合实力和业绩经验",
        "产品技术参数": "技术指标及测试情况",
        "POC测试": "技术指标及测试情况",
        "实施方案": "项目实施及维保服",
        "维保服务": "项目实施及维保服",
        "增值服务": "增值服务",
    }
    assert outcome.group_totals == {
        "价格分": 30.0,
        "综合实力和业绩经验": 25.0,
        "技术指标及测试情况": 30.0,
        "项目实施及维保服": 10.0,
        "增值服务": 5.0,
    }
    assert sum(item.score_weight for item in outcome.items) == 100.0


def test_same_page_proximity_never_assigns_poc_to_next_group() -> None:
    outcome = reconcile_item_group_relations(
        _doc2511_items(),
        _doc2511_rows(),
        require_authority=True,
    )

    poc = next(item for item in outcome.items if item.score_item_name == "POC测试")
    assert poc.source_pages == []
    assert poc.volume_label == "技术指标及测试情况"
    assert poc.volume_label != "项目实施及维保服"


def test_incomplete_group_evidence_clears_unverified_labels() -> None:
    outcome = reconcile_item_group_relations(
        _doc2511_items(),
        _doc2511_rows(include_technical_total=False),
        require_authority=True,
    )

    assert outcome.authoritative is False
    assert outcome.reason == "group_declared_score_missing"
    assert all(item.volume_label is None for item in outcome.items)
    assert outcome.cleared_count == 8


def test_group_totals_project_to_volume_defs_with_cross_page_score() -> None:
    """: 闭合组分值（含跨页缝合的30）投影为卷定义，取代模型header自述。"""
    defs = volume_defs_from_group_totals(
        {
            "价格分": 30.0,
            "综合实力和业绩经验": 25.0,
            "技术指标及测试情况": 30.0,
            "项目实施及维保服": 10.0,
            "增值服务": 5.0,
        }
    )

    assert set(defs) == {"price", "composite", "technical"}
    assert defs["technical"].volume_max == 30.0
    assert defs["technical"].weight == 0.3
    assert defs["technical"].label == "技术指标及测试情况"
    assert defs["price"].volume_max == 30.0
    assert defs["composite"].volume_max == 25.0


def test_ambiguous_volume_mapping_drops_conflicting_defs() -> None:
    """同一卷枚举命中多个闭合组时歧义弃用，其余组不受连坐。"""
    defs = volume_defs_from_group_totals(
        {
            "技术方案": 40.0,
            "技术指标": 30.0,
            "价格评审": 30.0,
        }
    )

    assert set(defs) == {"price"}
    assert defs["price"].volume_max == 30.0


def test_unmappable_or_non_positive_totals_are_skipped() -> None:
    """映射不到卷枚举或非正分值的组不产出定义，宁缺不错。"""
    assert volume_defs_from_group_totals({"项目实施及维保服": 10.0}) == {}
    assert volume_defs_from_group_totals({"价格分": 0.0}) == {}
    assert volume_defs_from_group_totals({}) == {}
