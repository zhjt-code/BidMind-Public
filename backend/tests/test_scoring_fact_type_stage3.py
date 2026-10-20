"""阶段3 IR显式化防御测试: penalty调整项建模 + score_conflict诊断。

职责: 锁定纯扣分区间不误读为正分/不静默丢弃、调整项豁免缺失统计、
      规则文本显式满分声明与分值列矛盾时的诊断产出。
不做: 不测完整链路(sample_corpus golden 职责)、不测 LLM。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.embedded_score_parser import (  # noqa: E402
    parse_embedded_score_table,
)
from app.core.extractor.scoring.score_candidates import (  # noqa: E402
    detect_score_rule_conflict,
)
from app.core.extractor.scoring.score_column_semantics import (  # noqa: E402
    find_embedded_penalties,
    parse_score_range_max,
)
from app.core.extractor.scoring.score_totals import verify_score_totals  # noqa: E402
from app.schemas.requirement import ExtractedScoringItem  # noqa: E402

import pytest

pytestmark = pytest.mark.scoring


def test_find_embedded_penalties_parses_pure_deduction_range() -> None:
    """样本9形态: `不良行为处理情况通报（-30-0分）` → 最高扣30, 禁止读成30分正项。"""
    hits = find_embedded_penalties("不良行为处理情况通报（-30-0分）")
    assert hits == [("不良行为处理情况通报", 30.0)]
    # parse_score_range_max 不得把负区间当正区间
    assert parse_score_range_max("-30-0分") is None


def test_find_embedded_penalties_ignores_mixed_and_positive_ranges() -> None:
    assert find_embedded_penalties("环境影响评价（-4-4分）") == []
    assert find_embedded_penalties("绿色发展规划（0-2分）") == []


def test_embedded_parser_builds_penalty_item_with_zero_weight() -> None:
    header = ["评审要素", "评审内容"]
    data = [
        ["诚信评价 不良行为处理情况通报（-30-0分）",
         "存在不良行为的扣5-30分, 无不良行为不扣分"],
        ["绿色发展规划（0-2分）", "建立规划并执行较好的得2分"],
    ]
    items = parse_embedded_score_table(header, data)
    penalties = [i for i in items if i.fact_type == "penalty"]
    assert len(penalties) == 1
    assert penalties[0].score_weight == 0.0
    assert "不良行为处理情况通报" in penalties[0].score_item_name
    assert penalties[0].deduction_rules


def test_verify_score_totals_exempts_penalty_from_missing_count() -> None:
    items = [
        ExtractedScoringItem(
            category="TECH", score_item_name="技术方案", description="技术方案评分",
            score_weight=100.0, volume="technical",
        ),
        ExtractedScoringItem(
            category="QUAL", score_item_name="失信扣分", description="失信行为扣分项",
            score_weight=0.0, fact_type="penalty", volume="technical",
        ),
    ]
    report = verify_score_totals(items)
    assert report.total == 100.0
    assert report.missing_score_count == 0
    assert report.needs_review is False


def test_detect_score_rule_conflict_on_explicit_max_statement() -> None:
    # 整项前缀声明与列值不等 → 冲突
    hint = detect_score_rule_conflict(4.0, "本项满分6分, 按档位打分")
    assert hint is not None and "6分" in hint and "4分" in hint
    # 无前缀声明大于列值 → 冲突(子构成满分只会小于整项)
    hint_over = detect_score_rule_conflict(4.0, "综合评审, 满分10分")
    assert hint_over is not None and "10分" in hint_over


def test_detect_score_rule_conflict_ignores_tier_ranges_and_sub_components() -> None:
    # 分档区间是同项内部档位, 不作冲突
    assert detect_score_rule_conflict(8.0, "优得6-8分, 良得3-6分, 一般0-3分") is None
    # 声明与列值一致不告警
    assert detect_score_rule_conflict(25.0, "其价格分为满分（25分）") is None
    # 1546实测: 无前缀"满分4分"是6分项的子构成(小于列值), 属合法不告警
    assert detect_score_rule_conflict(6.0, "每有一项证书得1分，满分4分。同一人不重复计分") is None
