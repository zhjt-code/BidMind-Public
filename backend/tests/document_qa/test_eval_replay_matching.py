"""评估装置对照匹配逻辑的防御测试

覆盖 match_findings_to_labels 的四类分桶(tp_hit/fp_hit/unlabeled/missing)与
一对一贪心约束; 夹具含两种不同 rule_code 实体(编号类/语言类), 防止匹配逻辑
只对单一检查器形态成立。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.document_qa_replay import match_findings_to_labels  # noqa: E402


def _finding(rule_code: str, page: int, excerpt: str) -> dict:
    return {
        "rule_code": rule_code,
        "title": "t",
        "message": "m",
        "evidence": {"items": [{"page": page, "excerpt": excerpt}]},
    }


def test_four_buckets_and_greedy_one_to_one() -> None:
    findings = [
        _finding("DOC-NUM-001", 10, "章节编号 3.2 缺失"),
        _finding("DOC-LANG-001", 4, "金额为人民币（大写） 0 元"),
        _finding("DOC-LAY-001", 57, "Na⁺,K⁺,Ca²⁺"),  # 未标注 -> unlabeled
    ]
    labels = [
        # 命中 tp(编号实体)
        {"rule_code": "DOC-NUM-001", "page": 10, "excerpt": "3.2 缺失", "verdict": "tp"},
        # 命中 fp(语言实体, 摘录归一化后子串匹配)
        {"rule_code": "DOC-LANG-001", "page": 4, "excerpt": "（大写） 0 元", "verdict": "fp"},
        # 未复现的 tp -> missing_tp(回归警报)
        {"rule_code": "DOC-STR-001", "page": 99, "excerpt": "空章节", "verdict": "tp"},
        # 未复现的 fp -> missing_fp(误报已消除)
        {"rule_code": "DOC-NUM-002", "page": 5, "excerpt": "重号", "verdict": "fp"},
    ]

    report = match_findings_to_labels(findings, labels)
    counts = report["counts"]

    assert counts["tp_hit"] == 1
    assert counts["fp_hit"] == 1
    assert counts["unlabeled"] == 1
    assert counts["missing_tp"] == 1
    assert counts["missing_fp"] == 1
    assert report["labeled_precision"] == 0.5
    assert report["unlabeled"][0]["rule_code"] == "DOC-LAY-001"
    assert report["missing_tp"][0]["rule_code"] == "DOC-STR-001"


def test_page_and_rule_code_both_required() -> None:
    findings = [_finding("DOC-NUM-001", 10, "编号 3.2 缺失")]
    # 页码不符不命中
    report = match_findings_to_labels(
        findings,
        [{"rule_code": "DOC-NUM-001", "page": 11, "excerpt": "3.2", "verdict": "fp"}],
    )
    assert report["counts"]["fp_hit"] == 0
    assert report["counts"]["missing_fp"] == 1
    # rule_code 不符不命中
    report = match_findings_to_labels(
        findings,
        [{"rule_code": "DOC-LANG-001", "page": 10, "excerpt": "3.2", "verdict": "fp"}],
    )
    assert report["counts"]["fp_hit"] == 0


def test_one_finding_consumed_once() -> None:
    findings = [_finding("DOC-NUM-002", 5, "同页编号「二」重复")]
    labels = [
        {"rule_code": "DOC-NUM-002", "page": 5, "excerpt": "编号「二」", "verdict": "fp"},
        {"rule_code": "DOC-NUM-002", "page": 5, "excerpt": "「二」重复", "verdict": "tp"},
    ]
    report = match_findings_to_labels(findings, labels)
    # 第一条标注消费掉唯一 finding, 第二条落 missing
    assert report["counts"]["fp_hit"] == 1
    assert report["counts"]["tp_hit"] == 0
    assert report["counts"]["missing_tp"] == 1
