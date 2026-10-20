"""评分规则列窄回填防御测试。"""
from app.core.extractor.scoring.scoring_rule_evidence import (
    backfill_rules_from_payload_roles,
    backfill_rules_from_role_grid,
)
from app.schemas.requirement import ExtractedScoringItem

import pytest

pytestmark = pytest.mark.scoring


def _item() -> ExtractedScoringItem:
    return ExtractedScoringItem(
        category="COMM",
        score_item_name="价格评审",
        description="价格评审",
        score_weight=30.0,
        volume="price",
    )


def test_unique_payload_role_row_backfills_rule_only() -> None:
    rows = [["1", "价格评审", "最低报价为评审基准价", "30"]]
    result = backfill_rules_from_payload_roles(
        [_item()],
        rows,
        {"name": 1, "rule": None, "score": 3},
        {"name": 1, "rule": 2, "score": 3, "group": None},
    )

    assert result[0].score_rules == ["最低报价为评审基准价"]
    assert result[0].description == "价格评审"


def test_missing_payload_roles_never_uses_lexical_guess() -> None:
    rows = [["1", "价格评审", "最低报价为评审基准价", "30"]]
    result = backfill_rules_from_payload_roles(
        [_item()],
        rows,
        {"name": 1, "rule": 2, "score": 3},
        None,
    )

    assert result[0].score_rules == []


def test_duplicate_physical_rows_are_ambiguous_even_when_rule_text_matches() -> None:
    rows = [
        ["1", "价格评审", "最低报价为评审基准价", "30"],
        ["1", "价格评审", "最低报价为评审基准价", "30"],
    ]
    result = backfill_rules_from_role_grid(
        [_item()],
        rows,
        {"name": 1, "rule": 2, "score": 3},
    )

    assert result[0].score_rules == []
