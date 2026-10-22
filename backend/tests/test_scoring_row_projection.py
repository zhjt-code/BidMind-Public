"""低样本评分行投影单元测试。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.parser.scoring_row_projection import project_scoring_row  # noqa: E402

pytestmark = pytest.mark.scoring


def test_project_scoring_row_accepts_single_score_name_and_rule() -> None:
    projected = project_scoring_row([
        "",
        "",
        "10分",
        "信誉",
        "信用评级为AA及以上的得10分，A得5分，未参评得1分。",
    ])

    assert projected is not None
    assert projected.name == "信誉"
    assert projected.score == 10.0
    assert "AA" in projected.rule


def test_project_scoring_row_rejects_multiple_score_cells() -> None:
    assert project_scoring_row(["", "5分", "信誉", "规则文本足够长", "10分"]) is None


def test_project_scoring_row_rejects_short_rule_text() -> None:
    assert project_scoring_row(["", "", "10分", "信誉", "AA得分"]) is None


def test_project_scoring_row_accepts_negative_adjustment_row() -> None:
    """: sample08 `扣分项(-3)` 退化行不得因分值为负被静默丢弃。"""
    projected = project_scoring_row([
        "扣分项",
        "",
        "",
        "",
        "",
        "-3",
        "根据《供应商黑名单管理办法》的有关规定，投标人被列入黑名单的扣3分。",
    ])

    assert projected is not None
    assert projected.name == "扣分项"
    assert projected.score == -3.0


def test_project_scoring_row_rejects_clause_number_as_score() -> None:
    """负例: 条款号 `3.4` 不得被当作调整项分值（须与真实分值格区分）。"""
    assert project_scoring_row([
        "3.4",
        "",
        "中标候选人推荐原则",
        "详见第二章投标人须知前附表内容说明。",
    ]) is None
