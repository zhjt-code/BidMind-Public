""" Level 3 合成样本：VLM 发明的分组标签穿透门禁假绿。

复刻项目3476(doc2511)：8 项叶子名称/分值全对(合计100)，但 VLM 把「价格分」
「增值服务」归入原文不存在的"技术卷"(照抄提示词 few-shot 示例)，确认页分组
小计因此全乱，仅"综合实力和业绩经验25分"碰巧正确，且 verdict 转绿。

修复契约分两层：全局锚定先剥除原文不存在的标签；随后有序表格行按组边界与
分值闭合生成权威关系，纠正 POC测试→技术指标及测试情况，并恢复两个顶层单项组。
"""

from __future__ import annotations

import re
import unicodedata
from types import SimpleNamespace

from app.core.extractor.scoring.item_group_relation import (
    reconcile_item_group_relations,
)
from app.core.extractor.scoring.vlm.candidate_contract import (
    VlmScoringItemDraft,
    to_extracted_items,
)
from app.core.extractor.scoring.vlm.candidate_gates import (
    sanitize_unanchored_group_labels,
)

# doc2511 第20-21页文本层片段(与 rescue_input_builder._normalize_page_texts 同口径压缩)
_PAGE_TEXT = (
    "第三章 评标办法 投标文件评审项目 评分要求 对应页码 "
    "1 价格分 30分 A、评标价的计算 报价得分=（评标基准价/投标人的评标价）×30 "
    "2 综合实力和业绩经验 25分 产品制造商综合实力 5分 业绩案例 20分 "
    "3 技术指标及测试情况 产品技术参数 15分 POC测试 15分 "
    "4 项目实施及维保服 10分 实施方案 （5分） 维保服务 5分 "
    "5 增值服务 5分"
)
_ANCHOR = re.sub(r"\s+", "", unicodedata.normalize("NFKC", _PAGE_TEXT)).lower()

# VLM 第2轮实际输出形态: 叶子与分值全对, 分组标签两条伪造("技术卷")
_DRAFTS = [
    VlmScoringItemDraft(
        score_item_name="价格分", score_weight=30.0, group_path=["技术卷"],
        score_rules=["报价得分=（评标基准价/投标人的评标价）×30"],
    ),
    VlmScoringItemDraft(
        score_item_name="产品制造商综合实力", score_weight=5.0,
        group_path=["综合实力和业绩经验"], score_rules=["优良4-5分"],
    ),
    VlmScoringItemDraft(
        score_item_name="业绩案例", score_weight=20.0,
        group_path=["综合实力和业绩经验"], score_rules=["每案例2分"],
    ),
    VlmScoringItemDraft(
        score_item_name="产品技术参数", score_weight=15.0,
        group_path=["技术指标及测试情况"], score_rules=["全部满足得满分15分"],
    ),
    VlmScoringItemDraft(
        score_item_name="POC测试", score_weight=15.0,
        group_path=["项目实施及维保服"], score_rules=["优良10-15分"],
    ),
    VlmScoringItemDraft(
        score_item_name="实施方案", score_weight=5.0,
        group_path=["项目实施及维保服"], score_rules=["优良4-5分"],
    ),
    VlmScoringItemDraft(
        score_item_name="维保服务", score_weight=5.0,
        group_path=["项目实施及维保服"], score_rules=["优良4-5分"],
    ),
    VlmScoringItemDraft(
        score_item_name="增值服务", score_weight=5.0, group_path=["技术卷"],
        score_rules=["额外追加的开发人月"],
    ),
]

_ROWS = [
    SimpleNamespace(chunk_index=49, page_num=20, content="表格第2行：1 | 价格分 30分 | 报价规则"),
    SimpleNamespace(
        chunk_index=50,
        page_num=20,
        content="表格第3行：2 | 综合实力和业绩经验 25分 | 产品制造商综合实力 5分 | 评分规则",
    ),
    SimpleNamespace(chunk_index=51, page_num=20, content="表格第4行：业绩案例 20分 | 评分规则"),
    SimpleNamespace(
        chunk_index=52,
        page_num=20,
        content="表格第5行：3 | 技术指标及测试情况 | 产品技术参数 15分 | 评分规则",
    ),
    SimpleNamespace(chunk_index=54, page_num=21, content="表格第1行：30分 | 跨页续行"),
    SimpleNamespace(chunk_index=55, page_num=21, content="表格第2行：POC测试 15分 | 评分规则"),
    SimpleNamespace(
        chunk_index=56,
        page_num=21,
        content="表格第3行：4 | 项目实施及维保服 10分 | 实施方案（5分） | 评分规则",
    ),
    SimpleNamespace(chunk_index=57, page_num=21, content="表格第4行：维保服务 5分 | 评分规则"),
    SimpleNamespace(chunk_index=58, page_num=21, content="表格第5行：5 | 增值服务 5分 | 评分规则"),
]


def test_bug988_fabricated_volume_label_stripped_real_labels_kept() -> None:
    items = to_extracted_items(_DRAFTS)
    sanitized, stripped = sanitize_unanchored_group_labels(items, _ANCHOR)

    by_name = {item.score_item_name: item for item in sanitized}
    # 伪造的"技术卷"必须剥除(原文146个chunk零命中)
    assert by_name["价格分"].volume_label is None
    assert by_name["增值服务"].volume_label is None
    assert sorted(stripped) == ["价格分: 技术卷", "增值服务: 技术卷"]
    # 原文真实存在的标签保留, 不误伤
    assert by_name["业绩案例"].volume_label == "综合实力和业绩经验"
    assert by_name["产品技术参数"].volume_label == "技术指标及测试情况"
    assert by_name["维保服务"].volume_label == "项目实施及维保服"
    # 名称与分值轴零改动(剥标签不拒项)
    assert sum(item.score_weight for item in sanitized) == 100.0
    assert len(sanitized) == 8


def test_bug988_structural_relation_recovers_complete_group_composition() -> None:
    items = to_extracted_items(_DRAFTS)
    sanitized, _stripped = sanitize_unanchored_group_labels(items, _ANCHOR)

    outcome = reconcile_item_group_relations(
        sanitized,
        _ROWS,
        require_authority=True,
    )

    assert outcome.authoritative is True
    assert outcome.group_totals == {
        "价格分": 30.0,
        "综合实力和业绩经验": 25.0,
        "技术指标及测试情况": 30.0,
        "项目实施及维保服": 10.0,
        "增值服务": 5.0,
    }
    by_name = {item.score_item_name: item.volume_label for item in outcome.items}
    assert by_name["POC测试"] == "技术指标及测试情况"
    assert by_name["价格分"] == "价格分"
    assert by_name["增值服务"] == "增值服务"
