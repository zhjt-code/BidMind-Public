""": 周建议列表必须附带中文展示字段，禁止只吐技术键。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_list_weekly_advice_includes_display_fields() -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.llm_weekly_advice import LlmWeeklyAdvice
    from app.services.ops_advisor.weekly_reliability_report import list_weekly_advice
    from sqlalchemy import delete

    marker = f"bug886_{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(LlmWeeklyAdvice).where(LlmWeeklyAdvice.target.in_(["extract", "bid_review.category_merge_decision"]))
        )
        db.add(
            LlmWeeklyAdvice(
                week_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
                scope="purpose",
                target="extract",
                advice_action="insufficient_data",
                recommended_config_id=None,
                demote_config_id=None,
                proposed_priority=None,
                evidence_json={"marker": marker},
                rule_id="no_candidates",
                status="pending",
            )
        )
        db.add(
            LlmWeeklyAdvice(
                week_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
                scope="node",
                target="bid_review.category_merge_decision",
                advice_action="keep",
                recommended_config_id="review_cloud_demo",
                demote_config_id=None,
                proposed_priority=None,
                evidence_json={"marker": marker},
                rule_id="node_readonly_best",
                status="pending",
            )
        )
        await db.commit()

        items = await list_weekly_advice(db, limit=50)
        purpose = next(i for i in items if i["scope"] == "purpose" and i["target"] == "extract")
        node = next(
            i for i in items
            if i["scope"] == "node" and i["target"] == "bid_review.category_merge_decision"
        )

        assert purpose["target_display"] == "提取"
        assert purpose["rule_label"] == "无可用配置候选"
        assert purpose["status_label"] == "待确认"
        assert purpose["advice_action_label"] == "数据不足"

        assert node["target_display"]
        assert node["target_display"] != node["target"] or "未登记" in node["target_display"]
        assert node["recommended_config_display"]
        assert "review_cloud_demo" not in node["recommended_config_display"] or "审查" in node["recommended_config_display"]
        assert node["rule_label"]
