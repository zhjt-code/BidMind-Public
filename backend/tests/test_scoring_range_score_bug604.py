""" 防御测试: 区间型分值列("0-30")与卷分组合并单元格的明细解析。

映射真实风险(项目1577/评分测试16): 分值列为"0-30/0-18"区间时旧逻辑全列解析 None →
明细路径被跳过 → 表落入嵌入式解析, 每卷折叠成 1 条 100 分卷级项, 卷内子项全部丢失。
真实页级验收见 test_scoring_assembler_golden.test_project1574_*; 本文件锁定单元语义。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.score_column_semantics import (  # noqa: E402
    parse_score_range_max,
)
from app.core.extractor.scoring.scoring_table_parser import (  # noqa: E402
    parse_scoring_table,
)

import pytest

pytestmark = pytest.mark.scoring

# 精简自项目1577 chunk 154383 的表结构(卷分组合并单元格 + 区间分值列)
WEIGHTED_VOLUME_DETAIL_TABLE = """[评分表 第42-43页]
|  | 权重 | 评审因素 | 分值 | 评 审 标 准 |
| --- | --- | --- | --- | --- |
| 资 信 标 （100 分） | 10% | 体系认证 | 0-30 | 每提供一项符合要求的证书得10分，本条满分30分。 |
|  |  | 投标人业绩 | 0-40 | 每提供1个类似业绩得10分，本条满分40分。 |
|  |  | 团队能力 | 0-30 | 项目组成员为3人得基本分10分，每增加1人加10分，最高30分。 |
| 技术 标 （100 分） | 50% | 项目实施方案 | 0-18 | 方案科学合理的得12（不含）-18分。 |
|  |  |  | 0-18 | 供货实施方案： 对技术方案完整性进行综合评审，可操作性一般的得6-12分。 |
|  |  | 售后服务 | 0-64 | 质保期内外服务承诺可行的得满分。 |
| 商 务 标 （100 分） | 40% | 投标报价 | 0-100 | 投标人报价等于评标基准价的，得100分。 |
"""


def test_parse_score_range_max_accepts_score_ranges():
    assert parse_score_range_max("0-30") == 30.0
    assert parse_score_range_max("0~30") == 30.0
    assert parse_score_range_max("0至18分") == 18.0
    assert parse_score_range_max(" 6 - 12 分 ") == 12.0


def test_parse_score_range_max_rejects_non_score_ranges():
    # 年份区间上限超 200 分值上限
    assert parse_score_range_max("2023-2025") is None
    # 上限必须大于下限
    assert parse_score_range_max("30-10") is None
    assert parse_score_range_max("30-30") is None
    # 规则正文/空值不解析
    assert parse_score_range_max("低于60分") is None
    assert parse_score_range_max("1-2页") is None
    assert parse_score_range_max("") is None


def test_range_score_detail_table_extracts_sub_items_not_volume_rows():
    """区间分值列必须走明细路径提取子项, 不得折叠为卷级 100 分项。"""
    result = parse_scoring_table(
        WEIGHTED_VOLUME_DETAIL_TABLE, section_path="评标方法及评价标准"
    )

    assert result.confident is True
    by_name = {it.score_item_name.replace(" ", ""): it for it in result.items}
    # 卷级合并单元格(资信标/技术标/商务标)不是评分项
    assert "资信标" not in by_name
    assert "技术标" not in by_name
    assert "商务标" not in by_name
    # 子项按区间上限计满分
    assert by_name["体系认证"].score_weight == 30.0
    assert by_name["投标人业绩"].score_weight == 40.0
    assert by_name["团队能力"].score_weight == 30.0
    assert by_name["项目实施方案"].score_weight == 18.0
    assert by_name["售后服务"].score_weight == 64.0
    assert by_name["投标报价"].score_weight == 100.0


def test_volume_group_cell_inherits_volume_for_following_rows():
    """卷分组合并单元格切换卷别, 组内后续子行继承; 资信标归 qualification。"""
    result = parse_scoring_table(
        WEIGHTED_VOLUME_DETAIL_TABLE, section_path="评标方法及评价标准"
    )
    by_name = {it.score_item_name.replace(" ", ""): it for it in result.items}
    assert by_name["体系认证"].volume == "qualification"
    assert by_name["投标人业绩"].volume == "qualification"
    assert by_name["团队能力"].volume == "qualification"
    assert by_name["项目实施方案"].volume == "technical"
    assert by_name["售后服务"].volume == "technical"
    assert by_name["投标报价"].volume == "commercial"
    # 同表权重列卷定义仍被解析(能力保留)
    assert result.volume_defs["qualification"].weight == 0.1
    assert result.volume_defs["technical"].weight == 0.5
    assert result.volume_defs["commercial"].weight == 0.4


def test_unnamed_sub_row_takes_name_from_rule_prefix():
    """内容列为空的合并单元格子行, 从规则列前缀"XXX："取子项名。"""
    result = parse_scoring_table(
        WEIGHTED_VOLUME_DETAIL_TABLE, section_path="评标方法及评价标准"
    )
    by_name = {it.score_item_name.replace(" ", ""): it for it in result.items}
    assert "供货实施方案" in by_name
    assert by_name["供货实施方案"].score_weight == 18.0
    assert by_name["供货实施方案"].volume == "technical"
