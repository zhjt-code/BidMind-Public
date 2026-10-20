""": 周建议列表默认每个 (scope, target) 只返回最新一周。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

_TARGET = "bug986_vision"
_NODE = "bug986.node_key"
_OLD_WEEK = datetime(2099, 1, 5, tzinfo=timezone.utc)
_NEW_WEEK = datetime(2099, 1, 12, tzinfo=timezone.utc)


def _row(
    *,
    week_start: datetime,
    status: str,
    rule_id: str,
    scope: str = "purpose",
    target: str = _TARGET,
) -> "LlmWeeklyAdvice":
    from app.models.llm_weekly_advice import LlmWeeklyAdvice

    return LlmWeeklyAdvice(
        week_start=week_start,
        scope=scope,
        target=target,
        advice_action="keep" if status == "pending" else "promote",
        recommended_config_id=None,
        demote_config_id=None,
        proposed_priority=None,
        evidence_json={"marker": "bug986_latest_board"},
        rule_id=rule_id,
        status=status,
    )


@pytest.mark.asyncio
async def test_list_weekly_advice_board_is_latest_per_target() -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.llm_weekly_advice import LlmWeeklyAdvice
    from app.services.ops_advisor.weekly_reliability_report import list_weekly_advice

    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(LlmWeeklyAdvice).where(
                LlmWeeklyAdvice.target.in_((_TARGET, _NODE))
            )
        )
        db.add(_row(week_start=_OLD_WEEK, status="applied", rule_id="demote_worst_degraded"))
        db.add(_row(week_start=_NEW_WEEK, status="pending", rule_id="best_already_preferred"))
        db.add(_row(
            week_start=_OLD_WEEK, status="pending", rule_id="node_readonly_best",
            scope="node", target=_NODE,
        ))
        db.add(_row(
            week_start=_NEW_WEEK, status="pending", rule_id="node_insufficient_sample",
            scope="node", target=_NODE,
        ))
        await db.commit()

        try:
            board = await list_weekly_advice(db, limit=100)
            purposes = [i for i in board if i["target"] == _TARGET]
            nodes = [i for i in board if i["target"] == _NODE]
            assert len(purposes) == 1
            assert purposes[0]["status"] == "pending"
            assert purposes[0]["rule_id"] == "best_already_preferred"
            assert purposes[0]["week_start"] is not None
            assert purposes[0]["week_start"].startswith("2099-01-12")
            assert len(nodes) == 1
            assert nodes[0]["rule_id"] == "node_insufficient_sample"

            that_week = await list_weekly_advice(db, week_start=_OLD_WEEK, limit=100)
            old_purpose = [i for i in that_week if i["target"] == _TARGET]
            assert len(old_purpose) == 1
            assert old_purpose[0]["status"] == "applied"
            assert old_purpose[0]["week_start"] is not None
            assert old_purpose[0]["week_start"].startswith("2099-01-05")
        finally:
            await db.execute(
                delete(LlmWeeklyAdvice).where(
                    LlmWeeklyAdvice.target.in_((_TARGET, _NODE))
                )
            )
            await db.commit()
