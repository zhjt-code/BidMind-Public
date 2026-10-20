"""红线指标比对逻辑。

实现 docs/regression_sample_spec.md §6.3 的红线检查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RedlineResult:
    name: str
    flow: str
    passed: bool
    baseline_value: Any = None
    candidate_value: Any = None
    threshold: str = ""
    detail: str = ""


@dataclass
class RedlineReport:
    results: list[RedlineResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)


def check_investigate_redlines(
    baseline_results: list[dict],
    candidate_results: list[dict],
) -> list[RedlineResult]:
    """投标审查红线检查。"""
    results: list[RedlineResult] = []

    b_not_miss = _count_disqualification_misses(baseline_results)
    c_not_miss = _count_disqualification_misses(candidate_results)
    results.append(RedlineResult(
        name="否决项漏判率",
        flow="investigate",
        passed=c_not_miss <= b_not_miss,
        baseline_value=b_not_miss,
        candidate_value=c_not_miss,
        threshold="不得高于基线",
    ))

    zero_tolerance = _count_not_to_fully(candidate_results)
    results.append(RedlineResult(
        name="不应通过的结论被判为通过",
        flow="investigate",
        passed=zero_tolerance == 0,
        candidate_value=zero_tolerance,
        threshold="零容忍",
    ))

    return results


def check_collusion_redlines(
    baseline_results: list[dict],
    candidate_results: list[dict],
) -> list[RedlineResult]:
    """串标审查红线检查。"""
    results: list[RedlineResult] = []

    c_fp = _count_false_positive_high(candidate_results)
    results.append(RedlineResult(
        name="一级证据误报率",
        flow="collusion",
        passed=c_fp == 0,
        candidate_value=c_fp,
        threshold="零容忍",
    ))

    b_miss_high = _count_missed_high(baseline_results)
    c_miss_high = _count_missed_high(candidate_results)
    results.append(RedlineResult(
        name="漏报 HIGH",
        flow="collusion",
        passed=c_miss_high <= b_miss_high,
        baseline_value=b_miss_high,
        candidate_value=c_miss_high,
        threshold="不得高于基线",
    ))

    return results


def check_writer_redlines(
    baseline_results: list[dict],
    candidate_results: list[dict],
) -> list[RedlineResult]:
    """标书编写红线检查。"""
    results: list[RedlineResult] = []

    c_tamper = _count_fixed_template_tamper(candidate_results)
    results.append(RedlineResult(
        name="fixed_template 章节篡改率",
        flow="writer",
        passed=c_tamper == 0,
        candidate_value=c_tamper,
        threshold="零容忍",
    ))

    return results


# ── 辅助统计函数 ──

def _count_disqualification_misses(results: list[dict]) -> int:
    count = 0
    for r in results:
        expected = r.get("expected_compliance_level")
        actual = r.get("compliance_level")
        is_critical = r.get("is_critical", False)
        if expected == "not" and is_critical and actual != "not":
            count += 1
    return count


def _count_not_to_fully(results: list[dict]) -> int:
    count = 0
    for r in results:
        if r.get("expected_compliance_level") == "not" and r.get("compliance_level") == "fully":
            count += 1
    return count


def _count_false_positive_high(results: list[dict]) -> int:
    count = 0
    for r in results:
        expected = r.get("expected_overall_risk", "")
        actual = r.get("overall_risk", "")
        if expected == "none" and actual == "high":
            count += 1
    return count


def _count_missed_high(results: list[dict]) -> int:
    count = 0
    for r in results:
        expected = r.get("expected_overall_risk", "")
        actual = r.get("overall_risk", "")
        if expected == "high" and actual != "high":
            count += 1
    return count


def _count_fixed_template_tamper(results: list[dict]) -> int:
    count = 0
    for r in results:
        if r.get("expected_node_type") == "fixed_template" and r.get("tampered", False):
            count += 1
    return count
