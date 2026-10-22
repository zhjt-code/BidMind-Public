"""审计脚本核心纯函数 TDD 测试（P1-4）。

职责: 验证 audit_scoring_legacy_baseline 脚本中解析函数和指标计算函数的正确性，
      为实现前的红灯测试。
不做: 不调用 LLM/DB、不运行完整提取链、不读取留出集 fixtures。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.audit_scoring_legacy_baseline import (  # noqa: E402
    compute_closure_status,
    compute_item_metrics,
    compute_item_pair_metrics,
    normalize_name_for_match,
    parse_truth_markdown,
)

pytestmark = pytest.mark.scoring

# ---------------------------------------------------------------------------
# 最小真值 Markdown 片段（与 sample_truth_training.md 格式完全一致）
# ---------------------------------------------------------------------------

_MINI_MD = """
## structure-03

- declared_total: 100
- volume_totals: unknown = 100（商务 25，技术服务 45，投标报价 30）

| sample_id | item_name | max_score | score_rule | source_page | lot | scheme | volume | fact_type |
|---|---:|---:|---|---:|---|---|---|---|
| structure-03 | 实力及信誉 | 5 | rule_text | 22 | unknown | 综合评分法 | unknown | base_score |
| structure-03 | 投标报价 | 30 | price_rule | 22-23 | unknown | 综合评分法 | unknown | price_formula |

## structure-04

- declared_total: 100

| sample_id | item_name | max_score | score_rule | source_page | lot | scheme | volume | fact_type |
|---|---:|---:|---|---:|---|---|---|---|
| structure-04 | 企业规模 | 2 | rule | 1 | unknown | 综合评分法 | unknown | base_score |
| structure-04 | 投标报价 | 60 | price | 1-2 | unknown | 综合评分法 | unknown | price_formula |

"""


# ---------------------------------------------------------------------------
# parse_truth_markdown
# ---------------------------------------------------------------------------


def test_parse_truth_markdown_returns_expected_keys() -> None:
    result = parse_truth_markdown(_MINI_MD)
    assert set(result.keys()) == {"03", "04"}


def test_parse_truth_markdown_declared_total() -> None:
    result = parse_truth_markdown(_MINI_MD)
    assert result["03"]["declared_total"] == 100.0
    assert result["04"]["declared_total"] == 100.0


def test_parse_truth_markdown_multischeme_total_from_volume_totals() -> None:
    content = """
## structure-09

- `declared_total`: 多套评分模板并列，单套模板各为100分，不横向相加。
- `volume_totals`: 技术评分标准/FWJS05=100；技术评分标准/FWJS16=100；技术评分标准/FWJS18=100；商务评分标准/FWSW01=100

| sample_id | item_name | max_score | score_rule | source_page | lot | scheme | volume | fact_type |
|---|---:|---:|---|---:|---|---|---|---|
| structure-09 | 技术投标文件的响应性 | 5 | rule | 19 | unknown | FWJS05 | 技术评分标准 | base_score |
"""
    result = parse_truth_markdown(content)
    assert result["09"]["declared_total"] == 400.0


def test_parse_truth_markdown_item_count() -> None:
    result = parse_truth_markdown(_MINI_MD)
    assert len(result["03"]["items"]) == 2
    assert len(result["04"]["items"]) == 2


def test_parse_truth_markdown_item_names() -> None:
    result = parse_truth_markdown(_MINI_MD)
    names_03 = [item["item_name"] for item in result["03"]["items"]]
    assert "实力及信誉" in names_03
    assert "投标报价" in names_03


def test_parse_truth_markdown_item_scores() -> None:
    result = parse_truth_markdown(_MINI_MD)
    items_03 = {item["item_name"]: item["max_score"] for item in result["03"]["items"]}
    assert items_03["实力及信誉"] == pytest.approx(5.0)
    assert items_03["投标报价"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# normalize_name_for_match
# ---------------------------------------------------------------------------


def test_normalize_strips_leading_trailing_whitespace() -> None:
    assert normalize_name_for_match("  技术方案  ") == "技术方案"


def test_normalize_removes_internal_spaces() -> None:
    assert normalize_name_for_match("施工单位企业 业绩 1") == "施工单位企业业绩1"


def test_normalize_removes_trailing_number_suffix() -> None:
    assert normalize_name_for_match("评分项(2)") == "评分项"
    assert normalize_name_for_match("评分项(12)") == "评分项"


def test_normalize_preserves_non_number_parentheses() -> None:
    # Chinese parentheses with non-number content should not be fully stripped
    result = normalize_name_for_match("技术方案（综合）")
    assert "技术方案" in result


def test_normalize_handles_empty_string() -> None:
    assert normalize_name_for_match("") == ""


# ---------------------------------------------------------------------------
# compute_item_metrics
# ---------------------------------------------------------------------------


def test_compute_metrics_perfect_match() -> None:
    metrics = compute_item_metrics(["A", "B"], ["A", "B"])
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["f1"] == pytest.approx(1.0)


def test_compute_metrics_partial_match() -> None:
    metrics = compute_item_metrics(["A", "B", "C"], ["A", "B", "D"])
    assert metrics["precision"] == pytest.approx(2 / 3, rel=0.01)
    assert metrics["recall"] == pytest.approx(2 / 3, rel=0.01)


def test_compute_metrics_empty_extracted() -> None:
    metrics = compute_item_metrics(["A", "B"], [])
    assert metrics["precision"] == pytest.approx(0.0)
    assert metrics["recall"] == pytest.approx(0.0)
    assert metrics["f1"] == pytest.approx(0.0)


def test_compute_metrics_empty_truth() -> None:
    metrics = compute_item_metrics([], ["A"])
    assert metrics["precision"] == pytest.approx(0.0)
    assert metrics["recall"] == pytest.approx(0.0)


def test_compute_metrics_returns_tp_counts() -> None:
    metrics = compute_item_metrics(["A", "B", "C"], ["A", "B"])
    assert metrics["true_positive"] == 2
    assert metrics["truth_count"] == 3
    assert metrics["extracted_count"] == 2


def test_compute_metrics_f1_formula() -> None:
    metrics = compute_item_metrics(["A", "B", "C"], ["A", "B"])
    p = metrics["precision"]
    r = metrics["recall"]
    expected_f1 = 2 * p * r / (p + r)
    assert metrics["f1"] == pytest.approx(expected_f1, rel=0.01)


# ---------------------------------------------------------------------------
# compute_item_pair_metrics（换轴重构 P0 主口径：名称+分值二元组）
# ---------------------------------------------------------------------------


def _truth(*pairs: tuple[str, float]) -> list[dict]:
    return [{"item_name": n, "max_score": s} for n, s in pairs]


def test_pair_metrics_perfect_match() -> None:
    metrics = compute_item_pair_metrics(
        _truth(("A", 10.0), ("B", 5.0)), [("A", 10.0), ("B", 5.0)]
    )
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["f1"] == pytest.approx(1.0)


def test_pair_metrics_score_mismatch_not_counted() -> None:
    # 名称对但分值错，不得计入 TP（旧名称口径会误判为全对）
    metrics = compute_item_pair_metrics(_truth(("A", 10.0)), [("A", 8.0)])
    assert metrics["true_positive"] == 0
    assert metrics["precision"] == pytest.approx(0.0)
    assert metrics["recall"] == pytest.approx(0.0)


def test_pair_metrics_compensating_errors_do_not_pass() -> None:
    # A项多提2分 + B项少提2分：总分守恒但两项均错，pair口径必须暴露
    metrics = compute_item_pair_metrics(
        _truth(("A", 10.0), ("B", 20.0)), [("A", 12.0), ("B", 18.0)]
    )
    assert metrics["true_positive"] == 0
    assert metrics["f1"] == pytest.approx(0.0)


def test_pair_metrics_tolerance_boundary() -> None:
    metrics = compute_item_pair_metrics(_truth(("A", 10.0)), [("A", 10.01)])
    assert metrics["true_positive"] == 1
    metrics_out = compute_item_pair_metrics(_truth(("A", 10.0)), [("A", 10.02)])
    assert metrics_out["true_positive"] == 0


def test_pair_metrics_duplicate_names_multiset() -> None:
    # 同名两项（如对称因素各5分）不做集合去重：真值2项只提1项 → recall=0.5
    metrics = compute_item_pair_metrics(
        _truth(("质量体系", 5.0), ("质量体系", 5.0)), [("质量体系", 5.0)]
    )
    assert metrics["truth_count"] == 2
    assert metrics["extracted_count"] == 1
    assert metrics["true_positive"] == 1
    assert metrics["recall"] == pytest.approx(0.5)
    assert metrics["precision"] == pytest.approx(1.0)


def test_pair_metrics_name_normalization_applies() -> None:
    metrics = compute_item_pair_metrics(
        _truth(("施工单位企业 业绩 1", 2.0)), [("施工单位企业业绩1", 2.0)]
    )
    assert metrics["true_positive"] == 1


def test_pair_metrics_empty_inputs() -> None:
    assert compute_item_pair_metrics([], [])["f1"] == pytest.approx(0.0)
    assert compute_item_pair_metrics(_truth(("A", 1.0)), [])["recall"] == pytest.approx(0.0)
    assert compute_item_pair_metrics([], [("A", 1.0)])["precision"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_closure_status
# ---------------------------------------------------------------------------


def test_closure_status_exactly_closed() -> None:
    status = compute_closure_status(100.0, 100.0)
    assert status["closed"] is True
    assert status["gap"] == pytest.approx(0.0)


def test_closure_status_with_gap() -> None:
    status = compute_closure_status(93.0, 100.0)
    assert status["closed"] is False
    assert status["gap"] == pytest.approx(-7.0)


def test_closure_status_within_tolerance() -> None:
    status = compute_closure_status(99.5, 100.0, tolerance=1.0)
    assert status["closed"] is True


def test_closure_status_outside_tolerance() -> None:
    status = compute_closure_status(98.0, 100.0, tolerance=1.0)
    assert status["closed"] is False


def test_closure_status_over_extracted() -> None:
    status = compute_closure_status(110.0, 100.0)
    assert status["closed"] is False
    assert status["gap"] == pytest.approx(10.0)
