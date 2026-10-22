""" Level 3 合成样本：跨页组总分被模型header谎报后反诬正确结果。

复刻项目3476(doc2511)：组「技术指标及测试情况(30分)」跨页——组名与首个子项
（产品技术参数15分）在第20页，「30分」声明格孤立掉到第21页首行。救援模型
header按单页视野把该卷总分报成15，弱子串锚定（页面恰有"POC测试15分"）放行，
volume_max=15盖到评分项后，确认页按卷校验以15为基准指控正确的30"多15分"。

修复契约：权威关系裁判的闭合组分值（跨页缝合后为30）投影为卷定义，
模型header自述总分整轴旁路，谎报的15无从进入基准。
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

# 本样本聚焦总分轴：叶子与分组标签均正确（已治标签轴），
# 错误只在header自述的组总分（技术指标及测试情况=15，应为30）。
_HEADER_CLAIMED_TECHNICAL_TOTAL = 15.0

_DRAFTS = [
    VlmScoringItemDraft(
        score_item_name="价格分", score_weight=30.0, group_path=["价格分"],
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
        group_path=["技术指标及测试情况"], score_rules=["优良10-15分"],
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
        score_item_name="增值服务", score_weight=5.0, group_path=["增值服务"],
        score_rules=["额外追加的开发人月"],
    ),
]

# doc2511 行块形态：chunk 54 是跨页续行的孤立「30分」格（第21页首行）
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


def test_bug993_cross_page_group_total_projection_beats_header_claim() -> None:
    outcome = reconcile_item_group_relations(
        to_extracted_items(_DRAFTS),
        _ROWS,
        require_authority=True,
    )

    assert outcome.authoritative is True
    # 跨页缝合：孤立「30分」格闭合到第20页开启的组
    assert outcome.group_totals["技术指标及测试情况"] == 30.0

    defs = volume_defs_from_group_totals(outcome.group_totals)

    assert defs["technical"].volume_max == 30.0
    assert defs["technical"].weight == 0.3
    assert defs["technical"].label == "技术指标及测试情况"
    assert defs["price"].volume_max == 30.0
    assert defs["composite"].volume_max == 25.0
    # header谎报的15无从进入任何卷基准
    assert all(
        vdef.volume_max != _HEADER_CLAIMED_TECHNICAL_TOTAL for vdef in defs.values()
    )
    # 映射不到卷枚举的组不产出定义（无基准即无假指控），不误配到其他卷
    assert "项目实施及维保服" not in {vdef.label for vdef in defs.values()}
    assert "增值服务" not in {vdef.label for vdef in defs.values()}
