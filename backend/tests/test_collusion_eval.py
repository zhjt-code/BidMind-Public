from eval.run_eval import (
    compute_token_metrics,
    run_collusion_extract_eval,
    run_collusion_pair_eval,
    run_collusion_project_eval,
)


def test_compute_token_metrics_returns_expected_scores() -> None:
    metrics = compute_token_metrics(
        predicted={"a", "b", "c"},
        expected={"b", "c", "d", "e"},
    )

    assert metrics["matched"] == 2
    assert round(metrics["precision"], 4) == round(2 / 3, 4)
    assert round(metrics["recall"], 4) == round(2 / 4, 4)
    assert round(metrics["f1"], 4) == 0.5714


def test_run_collusion_extract_eval_counts_field_level_matches() -> None:
    summary = run_collusion_extract_eval([
        {
            "id": "extract_case",
            "expected_output": {
                "collusion_extract": {
                    "documents": [
                        {
                            "document_id": "bid_a",
                            "company_name": "<ORG_23>",
                            "person_names": ['<PERSON_A>', '<PERSON_B>'],
                            "certificate_numbers": ["粤建安B(2020)0001"],
                            "total_price": "1020000元",
                        }
                    ]
                }
            },
            "actual_output": {
                "collusion_extract": {
                    "documents": [
                        {
                            "document_id": "bid_a",
                            "company_name": "<ORG_23>",
                            "person_names": ['<PERSON_A>'],
                            "certificate_numbers": ["粤建安B(2020)0001"],
                            "total_price": "1020000元",
                        }
                    ]
                }
            },
        }
    ])

    assert summary["count"] == 1
    assert summary["avg_precision"] == 1.0
    assert round(summary["avg_recall"], 4) == 0.8


def test_run_collusion_pair_eval_compares_dimensions_and_states() -> None:
    summary = run_collusion_pair_eval([
        {
            "id": "pair_case",
            "expected_output": {
                "collusion_pair": {
                    "pairs": [
                        {
                            "pair_id": "1:2",
                            "overall_risk": "high",
                            "risk_level": "high",
                            "decision_state": "decidable",
                            "review_state": "completed",
                            "hit_dimensions": ["D1", "D4"],
                        }
                    ]
                }
            },
            "actual_output": {
                "collusion_pair": {
                    "pairs": [
                        {
                            "pair_id": "1:2",
                            "overall_risk": "high",
                            "risk_level": "high",
                            "decision_state": "decidable",
                            "review_state": "completed",
                            "hit_dimensions": ["D1"],
                        }
                    ]
                }
            },
        }
    ])

    assert summary["count"] == 1
    assert summary["avg_precision"] == 1.0
    assert round(summary["avg_recall"], 4) == 0.8333


def test_run_collusion_project_eval_compares_group_risk() -> None:
    summary = run_collusion_project_eval([
        {
            "id": "project_case",
            "expected_output": {
                "collusion_project": {
                    "project_decision": "suspicious_found",
                    "project_risk_level": "high",
                    "overall_risk": "high",
                    "review_required": True,
                    "suspected_groups": [
                        {"document_ids": [101, 102], "risk_level": "high"}
                    ],
                }
            },
            "actual_output": {
                "collusion_project": {
                    "project_decision": "suspicious_found",
                    "project_risk_level": "high",
                    "overall_risk": "high",
                    "review_required": True,
                    "suspected_groups": [
                        {"document_ids": [101, 102], "risk_level": "medium"}
                    ],
                }
            },
        }
    ])

    assert summary["count"] == 1
    assert round(summary["avg_precision"], 4) == 0.8333
    assert round(summary["avg_recall"], 4) == 0.8333


def test_run_collusion_rule_eval_counts_ledger_and_hits() -> None:
    """验证 collusion_rule 口径：precision=1.0（actual 全命中），recall<1.0（expected 多一条账本条目）。

    expected token 集合（5 个）:
      "101:102|hit|D1-001"
      "101:102|hit_level|D1-001|first"
      "101:102|ledger|D2-021|not_checkable"
      "101:102|ledger|D5-002|unimplemented"   ← actual 缺少此条
      "101:102|gate_blocked|True"

    actual token 集合（4 个，全部属于 expected）:
      "101:102|hit|D1-001"
      "101:102|hit_level|D1-001|first"
      "101:102|ledger|D2-021|not_checkable"
      "101:102|gate_blocked|True"

    matched=4, precision=4/4=1.0, recall=4/5=0.8
    """
    from eval.run_eval import run_collusion_rule_eval

    summary = run_collusion_rule_eval([
        {
            "id": "rule_case",
            "expected_output": {
                "collusion_rule": {
                    "rule_hits": [
                        {"pair_id": "101:102", "rule_id": "D1-001", "level": "first"}
                    ],
                    "ledger": [
                        {"pair_id": "101:102", "rule_id": "D2-021", "status": "not_checkable"},
                        {"pair_id": "101:102", "rule_id": "D5-002", "status": "unimplemented"},
                    ],
                    "gate": [
                        {"pair_id": "101:102", "blocked": True}
                    ],
                }
            },
            "actual_output": {
                "collusion_rule": {
                    "rule_hits": [
                        {"pair_id": "101:102", "rule_id": "D1-001", "level": "first"}
                    ],
                    "ledger": [
                        {"pair_id": "101:102", "rule_id": "D2-021", "status": "not_checkable"},
                    ],
                    "gate": [
                        {"pair_id": "101:102", "blocked": True}
                    ],
                }
            },
        }
    ])

    assert summary["count"] == 1
    assert summary["avg_precision"] == 1.0
    assert round(summary["avg_recall"], 4) == 0.8
