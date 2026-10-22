"""表格结构真源与列角色资格赛防御测试。

职责: 锁定单元格跨度证据提取(跨列父表头/rowspan分组列)与 name/rule 数据资格赛
 的关键行为——项目1700"评分内容实为规则列"的词面反转必须被资格赛拦截。
不做: 不测装配器全链(sample_corpus golden 职责)、不测 PDF 解析。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.column_role_inference import (  # noqa: E402
    validate_name_rule_orientation,
)
from app.core.parser.table_grid_model import extract_column_structure  # noqa: E402

pytestmark = pytest.mark.scoring

# 项目1700 第28页形态: 5叶子列, 表头"评分标准"横跨 分组+因素 两列(anchor在槽0),
# 数据区 分组列(槽0) rowspan 合并(续行槽为 None)。
_X = [50.0, 89.6, 139.1, 466.1, 505.7, 545.4]


def _bbox(col_start: int, col_end: int, top: float, bottom: float):
    return (_X[col_start], top, _X[col_end], bottom)


def _p1700_like():
    data = [
        ["评分标准", "", "评分内容", "主观/客观/扣分", "满分分值"],
        ["综合实力（25%）", "相关业绩", "根据业绩进行评分" + "证明材料要求" * 20, "客观", "19.0"],
        ["", "企业资质", "具备资质证书得分" + "查询截图要求" * 20, "客观", "6.0"],
        ["技术实力（3%）", "专利证书", "根据专利证书数量评分" + "扫描件要求" * 20, "客观", "3.0"],
    ]
    cell_grid = [
        [_bbox(0, 2, 58, 105), None, _bbox(2, 3, 58, 105), _bbox(3, 4, 58, 105), _bbox(4, 5, 58, 105)],
        [_bbox(0, 1, 105, 415), _bbox(1, 2, 105, 261), _bbox(2, 3, 105, 261), _bbox(3, 4, 105, 261), _bbox(4, 5, 105, 261)],
        [None, _bbox(1, 2, 261, 415), _bbox(2, 3, 261, 415), _bbox(3, 4, 261, 415), _bbox(4, 5, 261, 415)],
        [_bbox(0, 1, 415, 478), _bbox(1, 2, 415, 478), _bbox(2, 3, 415, 478), _bbox(3, 4, 415, 478), _bbox(4, 5, 415, 478)],
    ]
    return data, cell_grid


def test_extract_column_structure_detects_spanning_header_and_rowspan_group() -> None:
    data, cell_grid = _p1700_like()
    evidence = extract_column_structure(data, cell_grid, header_row_index=0)
    assert evidence is not None
    assert evidence.leaf_column_count == 5
    # "评分标准"横跨 col0-col1 → 两列均为 spanning, 词面不代表单列语义
    assert {0, 1}.issubset(evidence.spanning_header_cols)
    # 叶子级表头: col2/col3/col4 有独立文本, col0/col1 为空
    assert evidence.leaf_header_texts[0] == ""
    assert evidence.leaf_header_texts[2] == "评分内容"
    assert evidence.leaf_header_texts[4] == "满分分值"
    # 分组列(col0)存在 rowspan 合并证据
    assert 0 in evidence.rowspan_merge_cols


def test_extract_column_structure_returns_none_without_grid() -> None:
    data, _ = _p1700_like()
    assert extract_column_structure(data, None, header_row_index=0) is None
    assert extract_column_structure(data, [], header_row_index=0) is None


def test_orientation_qualification_rejects_reversed_name_rule() -> None:
    """项目1700反转形态: 词面把 name 绑到长规则列(col2)、rule 绑到短列(col0)必须判死。"""
    data, _ = _p1700_like()
    rows = data[1:]
    assert validate_name_rule_orientation(rows, name_col=2, rule_col=0) is False
    # 正确取向(name=短名列col1, rule=长文本col2)通过
    assert validate_name_rule_orientation(rows, name_col=1, rule_col=2) is True


def test_orientation_qualification_tolerates_missing_roles() -> None:
    data, _ = _p1700_like()
    rows = data[1:]
    assert validate_name_rule_orientation(rows, name_col=None, rule_col=2) is True
    assert validate_name_rule_orientation(rows, name_col=1, rule_col=None) is True
