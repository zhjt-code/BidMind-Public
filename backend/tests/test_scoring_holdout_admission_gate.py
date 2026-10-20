import pytest
from backend.scripts.run_scoring_holdout_admission import (
    classify_sample_safety,
    parse_sealed_truth_items,
    summarize_gate,
)

pytestmark = pytest.mark.scoring


def _sealed(*blocks: str) -> str:
    return "\n".join(blocks)


def _item(sample: str, name: str, score: float, fact_type: str) -> str:
    return "\n".join(
        [
            f"- sample_id: structure-{sample}",
            f'  item_name: "{name}"',
            f"  max_score: {score}",
            f'  fact_type: "{fact_type}"',
        ]
    )


# ===== 真值分轴求和=====


def test_final_score_formula_row_is_not_summed_into_leaf_total() -> None:
    """structure-24 形态: 三卷各100 + 一行"最终评标得分"汇总公式100。

    汇总公式是三卷按 30%/15%/55% 折算后的**同一批分值的再表达**, 与叶子评分项
    不在同一轴。旧实现无视 fact_type 一律相加, 把基线算成 400, 该错误基线又反过来
    让"多方案"误报被验收为正确行为。
    """
    content = _sealed(
        _item("24", "设计任务书优化", 100, "subjective_band"),
        _item("24", "信誉评价结果", 100, "fixed_full_score"),
        _item("24", "投标报价", 100, "formula_price"),
        _item("24", "最终评标得分", 100, "final_score_formula"),
    )

    parsed = parse_sealed_truth_items(content)["24"]

    assert parsed["declared_total"] == 300.0, "叶子合计只计叶子事实"
    assert parsed["aggregate_total"] == 100.0, "汇总层分值单列, 不得静默丢弃"
    assert len(parsed["items"]) == 4, "条目明细仍需完整保留供逐项比对"


def test_score_group_row_is_treated_as_aggregate_axis() -> None:
    """round2 真值里的 `score_group` 同样是组小计, 不能与其子项相加。"""
    content = _sealed(
        _item("35", "子项A", 30, "score_item"),
        _item("35", "子项B", 20, "score_item"),
        _item("35", "技术组小计", 50, "score_group"),
    )

    parsed = parse_sealed_truth_items(content)["35"]

    assert parsed["declared_total"] == 50.0
    assert parsed["aggregate_total"] == 50.0


def test_leaf_only_sample_keeps_previous_total() -> None:
    """无汇总层的样本口径不变(零回归): 全部叶子照常相加。"""
    content = _sealed(
        _item("23", "承包人建议书——设计深化", 4, "subjective_tiered"),
        _item("23", "投标报价", 50, "formula_price"),
    )

    parsed = parse_sealed_truth_items(content)["23"]

    assert parsed["declared_total"] == 54.0
    assert parsed["aggregate_total"] == 0.0


def test_exact_sample_is_verified() -> None:
    row = classify_sample_safety(
        item_errors=0,
        score_gap=0.0,
        validation_flags=0,
        runtime_error=0,
        extracted_count=5,
        truth_count=5,
    )

    assert row == "VERIFIED"


def test_imperfect_but_visible_sample_is_provisional() -> None:
    row = classify_sample_safety(
        item_errors=3,
        score_gap=-10.0,
        validation_flags=1,
        runtime_error=0,
        extracted_count=4,
        truth_count=7,
    )

    assert row == "PROVISIONAL"


def test_runtime_error_is_failed() -> None:
    assert (
        classify_sample_safety(
            item_errors=0,
            score_gap=0.0,
            validation_flags=1,
            runtime_error=1,
            extracted_count=0,
            truth_count=5,
        )
        == "FAILED"
    )


def test_silent_empty_is_failed() -> None:
    assert (
        classify_sample_safety(
            item_errors=5,
            score_gap=-100.0,
            validation_flags=1,
            runtime_error=0,
            extracted_count=0,
            truth_count=5,
        )
        == "FAILED"
    )


def test_gate_passes_with_verified_and_provisional_only() -> None:
    gate = summarize_gate(
        [
            {"status": "VERIFIED", "key": "01", "item_errors": 0, "score_gap": 0.0, "validation_flags": 0, "runtime_error": 0, "precision": 1.0, "recall": 1.0},
            {"status": "PROVISIONAL", "key": "02", "item_errors": 2, "score_gap": -5.0, "validation_flags": 1, "runtime_error": 0, "precision": 0.5, "recall": 0.4},
        ]
    )

    assert gate["overall"] == "PASS"
    assert gate["verified_count"] == 1
    assert gate["provisional_count"] == 1
    assert gate["failed_ids"] == []


def test_gate_fails_with_any_failed_sample() -> None:
    gate = summarize_gate(
        [
            {"status": "VERIFIED", "key": "01", "item_errors": 0, "score_gap": 0.0, "validation_flags": 0, "runtime_error": 0, "precision": 1.0, "recall": 1.0},
            {"status": "FAILED", "key": "02", "item_errors": 5, "score_gap": -100.0, "validation_flags": 1, "runtime_error": 0, "precision": 0.0, "recall": 0.0},
        ]
    )

    assert gate["overall"] == "FAIL"
    assert gate["failed_ids"] == ["02"]
