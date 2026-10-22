""": 章节裁决候选封顶/分批/只合并绑定项。"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.writer.chapter_judge_runtime import (
    cap_judge_candidates,
    iter_judge_batches,
    merge_bind_true,
    try_unique_rule_bind,
)


def test_iter_judge_batches_splits_forty_five_into_three() -> None:
    candidates = [SimpleNamespace(id=i, title=f"约束{i}") for i in range(1, 46)]
    batches = list(iter_judge_batches(candidates, 20))
    assert [len(b) for b in batches] == [20, 20, 5]
    assert batches[0][0].id == 1
    assert batches[2][-1].id == 45


def test_cap_and_batch_second_entity_performance_table() -> None:
    """同形态第二例: 业绩表约束用同一套 cap/batch，不写死章节名。"""
    clauses = [
        SimpleNamespace(id=100 + i, title=f"业绩表第{i}条") for i in range(1, 12)
    ]
    capped = cap_judge_candidates(clauses, 8)
    assert [c.id for c in capped] == list(range(101, 109))
    batches = list(iter_judge_batches(capped, 5))
    assert [len(b) for b in batches] == [5, 3]


def test_merge_bind_true_keeps_first_positive_only() -> None:
    merged = merge_bind_true([
        [
            {"clause_id": 12, "should_bind": True, "reason": "投标函模板"},
            {"clause_id": 13, "should_bind": False, "reason": "无关"},
        ],
        [
            {"clause_id": 12, "should_bind": True, "reason": "重复"},
            {"clause_id": 20, "should_bind": True, "reason": "业绩表格式"},
        ],
    ])
    assert [item["clause_id"] for item in merged] == [12, 20]
    assert merged[0]["reason"] == "投标函模板"


def test_try_unique_rule_bind_exclusive_and_volume_template() -> None:
    exclusive = SimpleNamespace(id=1, title="投标函")
    assert try_unique_rule_bind(
        [exclusive], exclusive_unique=True, volume_gated_unique=False,
    ) is exclusive
    template = SimpleNamespace(id=2, title="授权委托书")
    assert try_unique_rule_bind(
        [template], exclusive_unique=False, volume_gated_unique=True,
    ) is template
    assert try_unique_rule_bind(
        [exclusive, template], exclusive_unique=True, volume_gated_unique=True,
    ) is None
    assert try_unique_rule_bind(
        [exclusive], exclusive_unique=False, volume_gated_unique=False,
    ) is None
