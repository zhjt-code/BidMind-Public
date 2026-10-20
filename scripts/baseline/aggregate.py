"""基线指标汇总脚本（B0.6）。

从 prometheus_client default registry 读取当前进程已累积的指标，
产出 per-flow / per-guard 的 p50 / p95 / 总次数快照。

生产环境应通过 /metrics 端点或 Pushgateway 拉取；本地开发环境直接读
default registry（通过 import observability.metrics 触发注册）。

用法（最小）:
    python scripts/baseline/aggregate.py --output docs/baselines/week_N.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))


def collect_metrics_snapshot() -> dict[str, Any]:
    """从 prometheus_client default registry 抓取当前快照。

    调用前必须先导入 observability 与 ai.metrics，以便相关指标被注册。
    """
    from prometheus_client import REGISTRY

    snapshot: dict[str, Any] = {}
    for metric in REGISTRY.collect():
        name = metric.name
        samples = []
        for s in metric.samples:
            samples.append({
                "name": s.name,
                "labels": dict(s.labels),
                "value": s.value,
            })
        if samples:
            snapshot[name] = {
                "type": metric.type,
                "documentation": metric.documentation,
                "samples": samples,
            }
    return snapshot


def format_week_report(
    week_num: int,
    source: str,
    date_range: str,
    snapshot: dict[str, Any],
    notes: list[str] | None = None,
) -> str:
    """将指标快照格式化为周报 Markdown。"""
    notes = notes or []
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    guard_metrics = {k: v for k, v in snapshot.items() if k.startswith("guard_")}
    json_repair = snapshot.get("json_repair", {}) or snapshot.get("json_repair_total", {})
    empty_return = snapshot.get("empty_return", {}) or snapshot.get("empty_return_total", {})
    fallback = snapshot.get("fallback_trigger", {}) or snapshot.get("fallback_trigger_total", {})
    llm_calls = snapshot.get("llm_calls", {}) or snapshot.get("llm_calls_total", {})
    llm_latency = snapshot.get("llm_latency_seconds", {})

    lines = [
        f"# B0.6 基线周报 - Week {week_num}",
        "",
        f"**生成时间**: {ts}",
        f"**数据来源**: `{source}`",
        f"**统计区间**: {date_range}",
        "",
    ]

    if source.startswith("synthetic_smoke"):
        lines.extend([
            "> **警告**：本周报基于本地测试环境的合成流量，不等同于真实 4 周生产基线。",
            "> 数值仅用于证明 B0.4 / B0.5 埋点链路端到端可用。",
            "> 真实基线待 B0 合入生产后由运维按 I-008 重采集。",
            "",
        ])

    lines.append("## 一、Guard 观测指标")
    lines.append("")
    guard_inv = snapshot.get("guard_invocation", {}) or snapshot.get("guard_invocation_total", {})
    if guard_inv:
        lines.append("| guard | flow | verdict | calls |")
        lines.append("|---|---|---|---|")
        for s in guard_inv.get("samples", []):
            if not s["name"].endswith("_total"):
                continue
            lbl = s["labels"]
            lines.append(
                f"| {lbl.get('guard_name', '-')} | {lbl.get('flow', '-')} "
                f"| {lbl.get('verdict', '-')} | {s['value']:.0f} |"
            )
    else:
        lines.append("*（未采集到 guard 调用）*")
    lines.append("")

    lines.append("## 二、JSON 修复率")
    lines.append("")
    repairs = {
        s["labels"].get("repair_type", "-"): s["value"]
        for s in json_repair.get("samples", [])
        if s["name"].endswith("_total")
    }
    lines.append(f"- 合计触发: {sum(repairs.values()):.0f} 次")
    for rt, v in sorted(repairs.items()):
        lines.append(f"  - `{rt}`: {v:.0f}")
    lines.append("")

    lines.append("## 三、空返回率")
    lines.append("")
    empties = {
        (s["labels"].get("flow", "-"), s["labels"].get("purpose", "-")): s["value"]
        for s in empty_return.get("samples", [])
        if s["name"].endswith("_total")
    }
    lines.append(f"- 合计触发: {sum(empties.values()):.0f} 次")
    for (fl, pp), v in sorted(empties.items()):
        lines.append(f"  - flow=`{fl}` purpose=`{pp}`: {v:.0f}")
    lines.append("")

    lines.append("## 四、Fallback 触发")
    lines.append("")
    fbs = {
        (s["labels"].get("trigger_type", "-"), s["labels"].get("flow", "-")): s["value"]
        for s in fallback.get("samples", [])
        if s["name"].endswith("_total")
    }
    lines.append(f"- 合计触发: {sum(fbs.values()):.0f} 次")
    for (tt, fl), v in sorted(fbs.items()):
        lines.append(f"  - type=`{tt}` flow=`{fl}`: {v:.0f}")
    lines.append("")

    lines.append("## 五、LLM 调用与延迟（复用 ai/metrics.py）")
    lines.append("")
    calls = {
        (s["labels"].get("model", "-"),
         s["labels"].get("purpose", "-"),
         s["labels"].get("status", "-")): s["value"]
        for s in llm_calls.get("samples", [])
        if s["name"].endswith("_total")
    }
    lines.append(f"- LLM 调用总数: {sum(calls.values()):.0f}")
    for (m, p, st), v in sorted(calls.items()):
        lines.append(f"  - model=`{m}` purpose=`{p}` status=`{st}`: {v:.0f}")
    lines.append("")

    for n in notes:
        lines.append(f"- **备注**: {n}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="基线指标汇总")
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument("--source", default="synthetic_smoke",
                        help="数据源标签，生产环境用 'production'")
    parser.add_argument("--date-range", default="",
                        help="e.g. 2026-04-18 至 2026-04-24")
    parser.add_argument("--output", required=True)
    parser.add_argument("--note", action="append", default=[],
                        help="附注行，可多次提供")
    args = parser.parse_args()

    # 触发 observability 模块导入，确保指标已注册到 default registry
    from app.core.observability import metrics as obs_metrics  # noqa: F401

    snapshot = collect_metrics_snapshot()

    report_md = format_week_report(
        week_num=args.week,
        source=args.source,
        date_range=args.date_range or "本地测试",
        snapshot=snapshot,
        notes=args.note,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_md, encoding="utf-8")
    print(f"[baseline] Report written to {out_path}")

    # 同时导出 JSON 快照方便后续程序处理
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[baseline] Raw snapshot at {json_path}")


if __name__ == "__main__":
    main()
