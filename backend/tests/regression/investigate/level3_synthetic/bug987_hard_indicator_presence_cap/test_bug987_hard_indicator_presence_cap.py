""" Level 3 合成样本：技术卷硬指标被存在性压档。

复刻用户截图：交货期/售后均为「满足得2分，否则0」，逐档达标合计 4，
但 ScoringPresenceMaxGate 曾把预估压成 4×0.6=2.4 并追加技术方案套话。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.scoring_presence_max import ScoringPresenceMaxGate


def test_bug987_delivery_hard_indicator_keeps_full_marks() -> None:
    evidence = {
        "requirement_kind": "scoring_item",
        "is_key_review": True,
        "requirement_text": (
            "交货期满足要求得2分，否则0分；"
            "质量响应承诺2小时内响应、24小时内到达得2分，否则0分。"
        ),
        "scoring_metadata": {
            "volume": "technical",
            "max_score": 4.0,
            "score_rules": [
                "交货期满足要求得2分，否则0分",
                "质量响应2小时内响应、24小时内到达得2分，否则0分",
            ],
        },
        "score_weight": 4.0,
        "category": "commercial",
        "has_response": True,
        "response_content": "承诺交货期7天；售后5分钟响应、2小时到达。",
    }
    target = SimpleNamespace(
        compliance_level="fully",
        is_compliant=True,
        score=100,
        estimated_score=4.0,
        comment=(
            "投标人承诺交货期7天，满足交货要求应得2分；"
            "售后5分钟响应、2小时到达，优于招标要求应得2分。"
            "预估得分：4分。"
        ),
        rule_assessments=[
            {
                "rule_excerpt": "交货期满足得2分",
                "status": "met",
                "points": 2.0,
                "reason": "承诺7天",
                "evidence_pages": [12],
            },
            {
                "rule_excerpt": "质量响应得2分",
                "status": "met",
                "points": 2.0,
                "reason": "5分钟响应优于2小时",
                "evidence_pages": [12],
            },
        ],
    )
    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=target,
        evidence=evidence,
    )
    gate = ScoringPresenceMaxGate()
    assert gate.applies(ctx) is True
    verdict = gate.evaluate(ctx)
    assert verdict.verdict == "pass"
    assert verdict.suggested_patch is None
    assert target.estimated_score == 4.0
    assert "有技术方案不等于满分" not in (target.comment or "")
