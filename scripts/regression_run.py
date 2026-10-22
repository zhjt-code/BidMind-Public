#!/usr/bin/env python3
"""A/B 回归执行入口。

用法:
    python scripts/regression_run.py \\
        --flow {investigate|collusion|writer|all} \\
        --level {1|2|3|all} \\
        --baseline-branch main \\
        --candidate-branch main \\
        --output tests/regression/reports/b09_self_ab.html

规范: docs/regression_sample_spec.md §6.1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regression.report import generate_html_report
from regression.runner import run_ab_comparison


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B 回归执行")
    parser.add_argument("--flow", required=True, choices=["investigate", "collusion", "writer", "all"])
    parser.add_argument("--level", required=True, choices=["1", "2", "3", "all"])
    parser.add_argument("--baseline-branch", required=True)
    parser.add_argument("--candidate-branch", required=True)
    parser.add_argument("--baseline-snapshot", default=None, help="writer quality baseline snapshot JSON")
    parser.add_argument("--candidate-snapshot", default=None, help="writer quality candidate snapshot JSON")
    parser.add_argument("--output", required=True, help="HTML 报告输出路径")
    parser.add_argument(
        "--allow-synthetic", action="store_true",
        help="纳入 meta.yml 标注为 synthetic_* 的合成样本（偏离 #002）；默认跳过",
    )
    args = parser.parse_args()

    print(f"[regression_run] flow={args.flow} level={args.level} "
          f"baseline={args.baseline_branch} candidate={args.candidate_branch} "
          f"allow_synthetic={args.allow_synthetic}")

    result = run_ab_comparison(
        flow=args.flow,
        level=args.level,
        baseline_branch=args.baseline_branch,
        candidate_branch=args.candidate_branch,
        allow_synthetic=args.allow_synthetic,
        baseline_snapshot=args.baseline_snapshot,
        candidate_snapshot=args.candidate_snapshot,
    )

    diff = result["diff_summary"]
    redline = result["redline_report"]

    print(f"[regression_run] samples={result['sample_count']} records={result['record_count']}")
    print(f"[regression_run] diff_count={diff.diff_count} zero_diff={diff.is_zero_diff}")
    print(f"[regression_run] redlines: {len(redline.results)} checks, "
          f"all_passed={redline.all_passed}")

    html = generate_html_report(result)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"[regression_run] Report written to {output_path}")

    if not diff.is_zero_diff:
        print(f"[regression_run] WARNING: {diff.diff_count} field-level diffs found!")
        sys.exit(1)
    if not redline.all_passed:
        print(f"[regression_run] WARNING: {redline.failed_count} redline(s) failed!")
        sys.exit(1)

    print("[regression_run] SUCCESS: zero diff, all redlines passed.")


if __name__ == "__main__":
    main()
