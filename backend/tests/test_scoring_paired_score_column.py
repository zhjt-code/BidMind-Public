"""成对最大/最小值分值列消歧防御测试（归档 /Task3 能力移植 U3）。

职责: 锁定「表头明确成对声明最大值/最小值时必须选最大值列」这一判据及其护栏。
不做: 不测整表解析结果（由 T0 全字段基线与语料 golden 覆盖）。

真实缺陷形态: validation/04 与其副本 holdout_round2/38 原本选中最小值列，
9 个评分项中 6 项分值为 0、3 项取到偏小值，合计仅 28 分（应为 100）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.column_role_inference import (  # noqa: E402
    find_numeric_score_column,
)
from app.core.extractor.scoring.paired_score_column import (  # noqa: E402
    find_paired_header_columns,
    validate_and_select_max,
)


class TestFindPairedHeaderColumns:
    """成对表头识别：必须同时出现 max 与 min 同义词，且无独立权威分值列。"""

    def test_detects_max_min_pair(self):
        assert find_paired_header_columns(["评审因素", "最大值", "最小值"]) == (1, 2)

    def test_accepts_synonyms(self):
        assert find_paired_header_columns(["因素", "上限", "下限"]) == (1, 2)
        assert find_paired_header_columns(["因素", "最高值", "最低值"]) == (1, 2)
        assert find_paired_header_columns(["因素", "最高", "最低"]) == (1, 2)

    def test_unlisted_synonym_is_not_matched(self):
        """词集为精确全等匹配: "最高分"/"最低分" 未收录, 不得靠模糊匹配蒙混。

        这是有意的保守设计——扩词必须显式改 _MAX_TERMS/_MIN_TERMS 并补测试。
        """
        assert find_paired_header_columns(["因素", "最高分", "最低分"]) is None

    def test_authority_score_column_short_circuits(self):
        """有独立权威分值列时交回旧逻辑，避免成对配对覆盖真正的分值列。"""
        assert find_paired_header_columns(["因素", "分值", "最大值", "最小值"]) is None

    def test_single_side_only_returns_none(self):
        assert find_paired_header_columns(["因素", "最大值", "说明"]) is None
        assert find_paired_header_columns(["因素", "最小值", "说明"]) is None


class TestValidateAndSelectMax:
    """max 列数据充分性与 max>=min 约束。"""

    _ROWS = [
        ["技术能力", "10", "0"],
        ["企业业绩", "15", "9"],
        ["履约信誉", "15", "0"],
    ]

    def test_selects_max_when_sufficient(self):
        assert validate_and_select_max(1, 2, self._ROWS, min_score_rows=3) == 1

    def test_rejects_when_max_below_min(self):
        """任一可比行 max<min 即整表拒绝——说明列语义判反了。"""
        rows = [*self._ROWS, ["异常行", "3", "8"]]
        assert validate_and_select_max(1, 2, rows, min_score_rows=3) is None

    def test_rejects_when_positive_rows_insufficient(self):
        assert validate_and_select_max(1, 2, self._ROWS[:1], min_score_rows=3) is None

    def test_zero_rows_still_validate_max_ge_min(self):
        """0 分行不计正分命中，但仍参与 max>=min 校验。"""
        rows = [["A", "0", "0"], ["B", "10", "5"], ["C", "10", "5"], ["D", "10", "5"]]
        assert validate_and_select_max(1, 2, rows, min_score_rows=3) == 1


class TestFindNumericScoreColumnWiring:
    """接线：成对表头下选最大值列；无成对表头保持旧行为。"""

    def test_paired_header_selects_max_column(self):
        rows = [
            ["评审因素", "最大值", "最小值"],
            ["技术能力", "10", "0"],
            ["企业业绩", "15", "9"],
            ["履约信誉", "15", "0"],
        ]
        # 旧逻辑「最右侧非序号数值列」会选到最小值列(2)，成对判据必须纠正为 1
        assert find_numeric_score_column(rows, width=3, min_hits=3) == 1

    def test_without_paired_header_falls_back_to_legacy(self):
        rows = [
            ["评审因素", "评分标准", "分值"],
            ["技术能力", "优秀得10分", "10"],
            ["企业业绩", "每项得3分", "15"],
            ["履约信誉", "无不良记录", "15"],
        ]
        assert find_numeric_score_column(rows, width=3, min_hits=3) == 2
