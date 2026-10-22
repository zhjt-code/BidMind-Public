""": 混排表「技术部分，权重N%」组头须落入评分段并写入 volume_label。

职责: 锁定切段时段尾权重组头被剥离到下一段 prelude，解析后 unlabeled_sum=0。
不做: 不修 803/775、不改 scoped/lot、不依赖真实 PDF。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table
from app.core.parser.scoring_table_assembler import (
    _SegmentStreamBuilder,
    segment_to_grid,
)

pytestmark = pytest.mark.scoring

# 混排形态: 符合性表头 → 评审行 → 权重组头 → 真评分子表头 → 明细
_MIXED_TABLE = [
    ["内容", "评议内容", "评议标准", "是否合格"],
    ["形式 评审", "投标人名称", "名称是否一致", ""],
    ["资格 评审", "事业单位法人证书", "持有有效证书", ""],
    ["", "结 论", "", ""],
    ["技术部分，权重 60%", "", "", ""],
    ["序号", "内容", "分值", "评分标准"],
    ["1", "人员素质", "10", "团队主要人员6人及以上，得4分"],
    ["2", "施工业绩", "12", "有井筒腐蚀类或相关项目业绩"],
    ["3", "实验方案", "15", "每个测试项目有详细介绍"],
    ["4", "工期保障", "6", "90天内完成得6分"],
    ["5", "投标报价", "40", "降点率报价"],
    ["6", "履约能力", "10", "酌情打分"],
    ["7", "企业信誉", "7", "酌情打分"],
]


def test_weight_group_header_promoted_to_scoring_segment_prelude():
    """切到真评分子表头时，段尾「技术部分，权重60%」须进入新段 prelude，不得留在符合性段。"""
    builder = _SegmentStreamBuilder("评标办法")
    builder.feed_table(17, 0, _MIXED_TABLE)
    segments, _claimed = builder.finish()

    scoring = [
        s
        for s in segments
        if any("分值" in (c or "") for c in (s.header_row or []))
    ]
    assert scoring, "应产出以分值列为表头的评分段"
    prelude_blob = " ".join(
        " ".join(c for c in row if c) for row in scoring[0].prelude_rows
    ).replace(" ", "")
    assert "技术部分" in prelude_blob and "权重" in prelude_blob, (
        f"权重组头未进入评分段 prelude: {scoring[0].prelude_rows!r}"
    )


def test_mixed_table_volume_labels_clear_unlabeled_sum():
    """装配→解析后有分值项均须带 volume_label，禁止 60 分未归卷假黄条。"""
    builder = _SegmentStreamBuilder("评标办法")
    builder.feed_table(17, 0, _MIXED_TABLE)
    segments, _ = builder.finish()
    scoring = [
        s
        for s in segments
        if any("分值" in (c or "") for c in (s.header_row or []))
    ]
    assert scoring
    parsed = parse_scoring_table("", grid=segment_to_grid(scoring[0]))
    scored = [it for it in parsed.items if float(it.score_weight or 0) > 0]
    assert len(scored) >= 7
    unlabeled = [
        it
        for it in scored
        if not (getattr(it, "volume_label", None) or "").strip()
    ]
    unlabeled_sum = sum(float(it.score_weight or 0) for it in unlabeled)
    assert unlabeled_sum == 0.0, (
        f"unlabeled_sum={unlabeled_sum} names={[it.score_item_name for it in unlabeled]}"
    )
    # 技术明细应继承组标签（权重后缀可剥），禁止空标签
    technical_like = [
        it for it in scored if "人员" in (it.score_item_name or "") or "业绩" in (it.score_item_name or "")
    ]
    assert technical_like
    assert all("技术" in (it.volume_label or "") for it in technical_like)
