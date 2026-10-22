"""S.4.2 单元测试：stability_replay 的纯函数数学部分。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# stability_replay.py 放在仓库 scripts/regression/ 下，scripts 不是一个包，
# 所以这里用 importlib.util 按绝对路径加载。
_REPLAY_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "regression" / "stability_replay.py"
)
_spec = importlib.util.spec_from_file_location("stability_replay_module", _REPLAY_PATH)
assert _spec is not None and _spec.loader is not None
sr = importlib.util.module_from_spec(_spec)
sys.modules["stability_replay_module"] = sr
_spec.loader.exec_module(sr)


class TestLevenshteinDistance:
    def test_identical(self):
        assert sr.levenshtein_distance("abc", "abc") == 0

    def test_empty(self):
        assert sr.levenshtein_distance("", "") == 0
        assert sr.levenshtein_distance("abc", "") == 3
        assert sr.levenshtein_distance("", "abc") == 3

    def test_single_edit(self):
        assert sr.levenshtein_distance("abc", "abd") == 1
        assert sr.levenshtein_distance("abc", "ac") == 1

    def test_classic_kitten_sitting(self):
        assert sr.levenshtein_distance("kitten", "sitting") == 3


class TestLevenshteinRatio:
    def test_identical_returns_1(self):
        assert sr.levenshtein_ratio("abc", "abc") == 1.0

    def test_completely_different(self):
        assert sr.levenshtein_ratio("abc", "xyz") == 0.0

    def test_empty_both(self):
        assert sr.levenshtein_ratio("", "") == 1.0

    def test_half_overlap(self):
        # "abcd" vs "abxx" → distance=2, max_len=4, ratio=0.5
        assert sr.levenshtein_ratio("abcd", "abxx") == 0.5


class TestJaccardSimilarity:
    def test_identical_sets(self):
        assert sr.jaccard_similarity({1, 2, 3}, {1, 2, 3}) == 1.0

    def test_disjoint_sets(self):
        assert sr.jaccard_similarity({1, 2}, {3, 4}) == 0.0

    def test_empty_both_defined_as_1(self):
        assert sr.jaccard_similarity(None, None) == 1.0
        assert sr.jaccard_similarity([], []) == 1.0

    def test_empty_vs_nonempty(self):
        assert sr.jaccard_similarity([], [1]) == 0.0

    def test_partial_overlap(self):
        # A={1,2,3}, B={2,3,4} → ∩=2, ∪=4, jaccard=0.5
        assert sr.jaccard_similarity({1, 2, 3}, {2, 3, 4}) == 0.5

    def test_accepts_list_input(self):
        assert sr.jaccard_similarity([1, 2], [2, 3]) == pytest_approx(1 / 3)


class TestConclusionConsistency:
    def test_empty_runs_returns_1(self):
        assert sr.conclusion_consistency([], ("risk_level",)) == 1.0

    def test_single_run_returns_1(self):
        runs = [{1: {"risk_level": "high"}}]
        assert sr.conclusion_consistency(runs, ("risk_level",)) == 1.0

    def test_all_consistent(self):
        runs = [
            {1: {"risk_level": "high"}, 2: {"risk_level": "low"}},
            {1: {"risk_level": "high"}, 2: {"risk_level": "low"}},
            {1: {"risk_level": "high"}, 2: {"risk_level": "low"}},
        ]
        assert sr.conclusion_consistency(runs, ("risk_level",)) == 1.0

    def test_half_consistent(self):
        runs = [
            {1: {"risk_level": "high"}, 2: {"risk_level": "low"}},
            {1: {"risk_level": "high"}, 2: {"risk_level": "medium"}},  # item 2 飘
        ]
        assert sr.conclusion_consistency(runs, ("risk_level",)) == 0.5

    def test_multiple_fields_all_must_agree(self):
        runs = [
            {1: {"a": "x", "b": "y"}},
            {1: {"a": "x", "b": "Y_DIFF"}},  # b 飘 → 整条记不一致
        ]
        assert sr.conclusion_consistency(runs, ("a", "b")) == 0.0


class TestFailureStateConsistency:
    def test_all_consistent(self):
        runs = [
            {1: {"failure_state": "completed"}},
            {1: {"failure_state": "completed"}},
        ]
        assert sr.failure_state_consistency(runs) == 1.0

    def test_inconsistent(self):
        runs = [
            {1: {"failure_state": "completed"}},
            {1: {"failure_state": "needs_review"}},
        ]
        assert sr.failure_state_consistency(runs) == 0.0


class TestEvidenceJaccardMean:
    def test_single_run_returns_1(self):
        assert sr.evidence_jaccard_mean(
            [{1: {"source_pages": [1, 2]}}], ("source_pages",)
        ) == 1.0

    def test_two_identical_runs(self):
        runs = [
            {1: {"source_pages": [1, 2]}},
            {1: {"source_pages": [1, 2]}},
        ]
        assert sr.evidence_jaccard_mean(runs, ("source_pages",)) == 1.0

    def test_two_runs_half_overlap(self):
        runs = [
            {1: {"source_pages": [1, 2, 3]}},
            {1: {"source_pages": [2, 3, 4]}},  # jaccard = 0.5
        ]
        assert sr.evidence_jaccard_mean(runs, ("source_pages",)) == 0.5


class TestExplanationDriftRate:
    def test_single_run_returns_0(self):
        assert sr.explanation_drift_rate(
            [{1: {"risk_level": "high", "comment": "abc"}}],
            ("risk_level",), "comment",
        ) == 0.0

    def test_conclusion_different_not_counted(self):
        """结论不同的条目不计入漂移评估。"""
        runs = [
            {1: {"risk_level": "high", "comment": "abc"}},
            {1: {"risk_level": "low", "comment": "xxxxxxxxxxx"}},
        ]
        assert sr.explanation_drift_rate(
            runs, ("risk_level",), "comment",
        ) == 0.0

    def test_same_conclusion_completely_different_explanation(self):
        runs = [
            {1: {"risk_level": "high", "comment": "abc"}},
            {1: {"risk_level": "high", "comment": "xyz"}},  # ratio=0, drifted
        ]
        assert sr.explanation_drift_rate(
            runs, ("risk_level",), "comment", drift_threshold=0.3,
        ) == 1.0

    def test_same_conclusion_similar_explanation_not_drifted(self):
        runs = [
            {1: {"risk_level": "high", "comment": "abcdefghij"}},
            {1: {"risk_level": "high", "comment": "abcdefghij"}},
        ]
        assert sr.explanation_drift_rate(
            runs, ("risk_level",), "comment", drift_threshold=0.3,
        ) == 0.0


class TestCalculatorIntegration:
    def test_investigate_flow_end_to_end(self):
        """同一个项目 3 次回放，全字段一致 → 四维指标满分（漂移率=0）。"""
        sample_run = {
            101: {
                "risk_level": "high", "compliance_level": "not", "is_compliant": 0,
                "source_pages": [1, 2, 3],
                "comment": "未提供相关证明",
                "failure_state": "completed",
            },
            102: {
                "risk_level": "low", "compliance_level": "fully", "is_compliant": 1,
                "source_pages": [5],
                "comment": "符合",
                "failure_state": "completed",
            },
        }
        runs = [dict(sample_run), dict(sample_run), dict(sample_run)]
        calc = sr.StabilityMetricsCalculator()
        m = calc.compute(runs, flow="investigate", project_id=999)
        assert m.conclusion_rate == 1.0
        assert m.failure_state_rate == 1.0
        assert m.evidence_jaccard == 1.0
        assert m.explanation_drift_rate == 0.0
        assert m.total_items == 2
        assert m.total_runs == 3
        assert m.flow == "investigate"


def pytest_approx(value: float, rel: float = 1e-6):
    """简易浮点近似（不依赖 pytest.approx）。"""
    import pytest
    return pytest.approx(value, rel=rel)
