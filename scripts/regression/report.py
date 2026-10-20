"""HTML 报告生成器。

章节严格按 docs/regression_sample_spec.md §6.2 的 7 章。
"""

from __future__ import annotations

import html
import time
from typing import Any

from .diff import DiffSummary
from .redlines import RedlineReport


def generate_html_report(ab_result: dict[str, Any]) -> str:
    """根据 A/B 对比结果产出 HTML 报告。"""
    diff: DiffSummary = ab_result["diff_summary"]
    redline: RedlineReport = ab_result["redline_report"]
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    sections = [
        _section_overview(ab_result, diff),
        _section_redlines(redline),
        _section_field_diff(diff),
        _section_gate_behavior(),
        _section_failure_state(),
        _section_cost_latency(),
        _section_new_findings(diff),
    ]

    body = "\n".join(sections)

    synthetic_banner = ""
    if ab_result.get("has_synthetic"):
        synthetic_banner = (
            '<div style="background:#fee2e2;color:#991b1b;padding:12px;'
            'border:2px solid #dc2626;margin-bottom:16px;font-weight:bold;">'
            'SYNTHETIC SAMPLES INCLUDED — 本报告包含 AI 合成样本（偏离 #002）。'
            '禁止作为业务红线判定依据。详见 tests/regression/SYNTHETIC_NOTICE.md。'
            '</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>A/B Regression Report - {ab_result['flow']} L{ab_result['level']}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; color: #333; }}
h1 {{ border-bottom: 2px solid #2563eb; padding-bottom: 8px; }}
h2 {{ color: #1e40af; margin-top: 32px; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th, td {{ border: 1px solid #d1d5db; padding: 8px 12px; text-align: left; }}
th {{ background: #f3f4f6; }}
.pass {{ color: #059669; font-weight: bold; }}
.fail {{ color: #dc2626; font-weight: bold; }}
.high {{ background: #fee2e2; }}
.medium {{ background: #fef3c7; }}
.low {{ background: #f0fdf4; }}
.meta {{ color: #6b7280; font-size: 0.9em; }}
</style>
</head>
<body>
<h1>A/B Regression Report</h1>
{synthetic_banner}
<p class="meta">Generated: {ts} | Flow: {ab_result['flow']} | Level: {ab_result['level']} | Baseline: {ab_result['baseline_branch']} | Candidate: {ab_result['candidate_branch']}</p>
{body}
</body>
</html>"""


def _section_overview(ab_result: dict, diff: DiffSummary) -> str:
    status = '<span class="pass">PASS (zero diff)</span>' if diff.is_zero_diff else f'<span class="fail">DIFF ({diff.diff_count} fields)</span>'
    return f"""<h2>一、总览</h2>
<table>
<tr><th>样本项目数</th><td>{ab_result['sample_count']}</td></tr>
<tr><th>记录总数</th><td>{ab_result['record_count']}</td></tr>
<tr><th>比较字段数</th><td>{diff.total_fields_compared}</td></tr>
<tr><th>差异字段数</th><td>{diff.diff_count}</td></tr>
<tr><th>状态</th><td>{status}</td></tr>
</table>"""


def _section_redlines(redline: RedlineReport) -> str:
    if not redline.results:
        return "<h2>二、红线指标</h2><p>无样本数据，跳过红线检查。</p>"

    rows = ""
    for r in redline.results:
        cls = "pass" if r.passed else "fail"
        rows += f'<tr><td>{html.escape(r.flow)}</td><td>{html.escape(r.name)}</td><td>{r.baseline_value}</td><td>{r.candidate_value}</td><td>{html.escape(r.threshold)}</td><td class="{cls}">{"PASS" if r.passed else "FAIL"}</td></tr>\n'

    return f"""<h2>二、红线指标</h2>
<table>
<tr><th>流程</th><th>指标</th><th>基线值</th><th>候选值</th><th>红线</th><th>结果</th></tr>
{rows}
</table>
<p>整体: {'<span class="pass">全部通过</span>' if redline.all_passed else f'<span class="fail">{redline.failed_count} 项未通过</span>'}</p>"""


def _section_field_diff(diff: DiffSummary) -> str:
    if diff.is_zero_diff:
        return "<h2>三、字段级 diff</h2><p>零差异。</p>"

    rows = ""
    for d in sorted(diff.diffs, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.severity, 3)):
        rows += f'<tr class="{d.severity}"><td>{html.escape(str(d.sample_id))}</td><td>{html.escape(d.field_name)}</td><td>{html.escape(str(d.baseline_value))}</td><td>{html.escape(str(d.candidate_value))}</td><td>{d.severity}</td></tr>\n'

    return f"""<h2>三、字段级 diff</h2>
<table>
<tr><th>样本ID</th><th>字段</th><th>基线值</th><th>候选值</th><th>严重度</th></tr>
{rows}
</table>"""


def _section_gate_behavior() -> str:
    return "<h2>四、Gate 行为</h2><p>B0 阶段尚未接入 Gate Pipeline，本章留空。</p>"


def _section_failure_state() -> str:
    return "<h2>五、失败合同分布</h2><p>B0 阶段尚未产出 failure_state，本章留空。</p>"


def _section_cost_latency() -> str:
    return "<h2>六、成本与延迟</h2><p>B0 阶段仅自洽对比，无实际 LLM 调用成本数据，本章留空。</p>"


def _section_new_findings(diff: DiffSummary) -> str:
    if diff.is_zero_diff:
        return "<h2>七、新发现</h2><p>零差异，无新发现。</p>"
    high_diffs = [d for d in diff.diffs if d.severity == "high"]
    if not high_diffs:
        return "<h2>七、新发现</h2><p>无高严重度差异。</p>"
    items = "".join(f"<li>{html.escape(d.sample_id)}: {html.escape(d.field_name)} ({d.baseline_value} -> {d.candidate_value})</li>" for d in high_diffs[:20])
    return f"<h2>七、新发现</h2><p>高严重度差异 {len(high_diffs)} 项（前 20）:</p><ul>{items}</ul>"
