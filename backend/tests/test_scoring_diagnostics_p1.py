"""P1 评分解析诊断防御测试: 组基准闭环与可疑跳行 fail-visible。

映射真实风险: 评分项漏提时系统只能给出全局合计缺口, 不能指出哪个组/哪一行
解析失败。诊断只增量输出, 不改变评分项产物。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.group_closure import (  # noqa: E402
    build_group_closure_diagnostics,
)
from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402

pytestmark = pytest.mark.scoring


def test_group_closure_reports_declared_group_score_gap():
    """组小计声明 66 分、组内仅可解析 46 分时, 必须输出缺口诊断。"""
    grid = [
        ["评审项目", "", "分值", "评审要素"],
        ["技 术 部 分 66 分", "施工方案与技术措施", "最高 20分", "本项最高得20分"],
        ["", "质量管理体系与措施", "18分", "本项最高得18分"],
        ["", "安全管理体系与措施", "15分", "本项最高得15分"],
        ["", "环境保护管理体系与措施", "5分", "本项最高得5分"],
        ["", "工程进度计划与措施", "8分", "本项最高得8分"],
    ]

    diagnostics = build_group_closure_diagnostics(grid, score_col=2)

    assert diagnostics == [
        "组'技术部分'声明66分，表内可解析46分，缺口20分"
    ]


def test_group_closure_is_silent_when_declared_group_matches():
    """组声明与组内分值闭环时不输出噪声诊断。"""
    grid = [
        ["评审项目", "", "分值", "评审要素"],
        ["商 务 部 分 34 分", "报价", "30分", "价格公式"],
        ["", "类似项目业绩", "4分", "最高得4分"],
        ["技 术 部 分 66 分", "施工方案与技术措施", "20分", "最高得20分"],
        ["", "质量管理体系与措施", "18分", "最高得18分"],
        ["", "安全管理体系与措施", "15分", "最高得15分"],
        ["", "环境保护管理体系与措施", "5分", "最高得5分"],
        ["", "工程进度计划与措施", "8分", "最高得8分"],
    ]

    assert build_group_closure_diagnostics(grid, score_col=2) == []


def test_parse_scoring_table_exposes_suspicious_score_rows_as_diagnostics():
    """解析器遇到有对象名/规则但分值格不可解析的计分行时, 不得静默跳过。"""
    markdown = """[评分表 第33-34页]
| 评审项目 |  | 分值 | 评审要素 |
| --- | --- | --- | --- |
| 技 术 部 分 48 分 | 施工方案与技术措施 | 最高 20分 | 方案科学、合理，本项最高得 20 分。 |
|  | 安全管理体系与措施 | 15分 | 本项最高得15分。 |
|  | 环境保护管理体系与措施 | 5分 | 本项最高得5分。 |
|  | 工程进度计划与措施 | 8分 | 本项最高得8分。 |
"""

    parsed = parse_scoring_table(markdown, section_path="评分相关章节")

    assert parsed.confident is True
    assert [it.score_item_name for it in parsed.items] == [
        "安全管理体系与措施",
        "环境保护管理体系与措施",
        "工程进度计划与措施",
    ]
    assert any("施工方案与技术措施" in detail for detail in parsed.diagnostics)
    assert any("组'技术部分'声明48分，表内可解析28分，缺口20分" in detail for detail in parsed.diagnostics)
