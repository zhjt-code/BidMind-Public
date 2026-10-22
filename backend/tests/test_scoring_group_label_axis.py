# -*- coding: utf-8 -*-
"""原文组标签双侧落地端到端防御测试。

防御目标(映射真实风险 doc1933/项目2447):
- 声明侧与提取项侧任一缺标签, `group_rows` 即为空、分值全落 unlabeled——修一半
 等于没修。本文件锁定"解析 -> 捕获 -> 对账"整链闭合。
- 旧行为(词面路径)会把项级"满分30分"当文档总分, 高置信报 expected=30/gap=70 的
 假缺口; 修后须 expected=100/gap=0 且基准低置信(不得因父子闭合自我背书升为高置信)。
不做: 不读 holdout 真值、不触发 LLM/DB。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from test_group_column import _DOC1933_GRID  # noqa: E402

from app.core.extractor.scoring.embedded_score_parser import (  # noqa: E402
    parse_embedded_score_table,
)
from app.core.extractor.scoring.ledger.contracts import BasisLevel, LedgerStatus  # noqa: E402
from app.core.extractor.scoring.ledger.declaration_capture import (  # noqa: E402
    capture_scoring_declarations,
)
from app.core.extractor.scoring.ledger.ledger_reconciliation import (  # noqa: E402
    reconcile_scoring_ledger,
)

pytestmark = pytest.mark.scoring

_EXPECTED_GROUP_TOTALS = {
    "设计方案": 26.0,
    "施工方案": 30.0,
    "项目管理机构评分标准": 9.0,
    "投标报价评分标准": 30.0,
    "其他因素评分标准": 5.0,
}


def _parse_items():
    """按生产口径解析 doc1933 网格(表头分离, 走嵌入式分值路径)。"""
    header, data = _DOC1933_GRID[0], _DOC1933_GRID[1:]
    return parse_embedded_score_table(header, [list(row) for row in data])


def _entries(items):
    return [
        (
            str(getattr(i, "score_item_name", "") or ""),
            float(getattr(i, "score_weight", 0) or 0.0),
            str(getattr(i, "volume_label", None) or ""),
        )
        for i in items
    ]


# ---------------------------------------------------------------------------
# 提取项侧: volume_label 必须齐全且为原文组标签
# ---------------------------------------------------------------------------


def test_every_extracted_item_carries_original_group_label():
    """核心防回归: 19 项全部带 volume_label。

    旧行为下 `_infer_volume_label` 依赖 `volume_from_scoring_label` 词面判定,
    只有"投标报价评分标准"命中, 其余 18 项(70分)无标签 -> 账本 unlabeled_sum=70。
    """
    items = _parse_items()

    assert len(items) == 19
    labeled = [i for i in items if getattr(i, "volume_label", None)]
    assert len(labeled) == 19, "任一项无标签都会使该组在账本对账中缺额"


def test_item_labels_are_exactly_the_five_original_categories():
    """标签取值须严格等于原文 5 个大类, 不得被 refine 改写(否则归一键无法配对)。"""
    items = _parse_items()

    by_label: dict[str, float] = {}
    for item in items:
        label = str(item.volume_label or "")
        by_label[label] = round(by_label.get(label, 0.0) + float(item.score_weight), 4)

    assert by_label == _EXPECTED_GROUP_TOTALS


# ---------------------------------------------------------------------------
# 双侧闭合: 解析 -> 捕获 -> 对账
# ---------------------------------------------------------------------------


def _reconcile_doc1933():
    items = _parse_items()
    page_text = "\n".join(
        " | ".join(str(c or "") for c in row) for row in _DOC1933_GRID
    )
    grid = [[str(c or "") for c in row] for row in _DOC1933_GRID]
    capture = capture_scoring_declarations([(2, page_text)], [(2, grid)])
    return capture, reconcile_scoring_ledger(capture, _entries(items))


def test_doc1933_all_five_groups_close_with_zero_unlabeled():
    """5 组全部闭合、零未归组——即"归卷正确"的可断言定义。"""
    _capture, report = _reconcile_doc1933()

    assert len(report.group_rows) == 5
    assert report.unlabeled_sum == 0.0
    for row in report.group_rows:
        assert abs(row.gap) <= 0.01, f"{row.label} 未闭合: {row.gap}"
        assert row.declared == _EXPECTED_GROUP_TOTALS[row.label]
        assert row.extracted_sum == _EXPECTED_GROUP_TOTALS[row.label]


def test_doc1933_no_false_gap_and_item_level_full_score_not_used_as_basis():
    """假缺口消除: expected=100/gap=0, 且 30(项级满分)不在总分候选内。"""
    capture, report = _reconcile_doc1933()

    assert 30.0 not in capture.total_candidates
    assert report.expected_total == 100.0
    assert report.extracted_total == 100.0
    assert report.total_gap == 0.0


def test_doc1933_basis_stays_low_confidence_no_self_endorsement():
    """父子闭合不得自我背书为高置信: 无独立总分声明时基准恒低置信。

 ADR 红线"闭合不得作为凑分目标"的展示侧对应约束——若把组声明之和回填为总分
 候选, L1 会与自身比对而恒真, 产出无外部佐证的高置信结论(同源反模式)。
 """
    _capture, report = _reconcile_doc1933()

    assert report.basis_level is BasisLevel.L1_EXPLICIT_GROUP
    assert report.basis_low_confidence is True
    assert report.status is LedgerStatus.REVIEW_REQUIRED
