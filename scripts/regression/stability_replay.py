"""S.4.2 · 稳定性四维指标回放采集工具。

对同一项目跑 N 次完整流程（或从外部 JSON 读取 N 次产出），按 regression_sample_spec
§6.4 定义的四维指标计算稳定性并产出 HTML 报告。

运行模式：
  * ``--mode live``：真调 orchestrator（需要真实 LLM 配置；Lite 环境通常不具备）
  * ``--mode replay-json <dir>``：从外部准备好的 N 个 JSON 文件读取（推荐 Lite 验证）

用法示例:
  python scripts/regression/stability_replay.py \\
      --project-id 879 --flow investigate --runs 5 \\
      --output stability_report.html \\
      --mode replay-json ./samples/project_879/

本模块的核心数学部分（``StabilityMetricsCalculator``）与 CLI 解耦，可独立单元测试。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


Flow = Literal["investigate", "collusion", "writer", "all"]


# ---------------------------------------------------------------------------
# 纯函数数学核心 —— 可独立测试
# ---------------------------------------------------------------------------

def levenshtein_distance(a: str, b: str) -> int:
    """经典 O(|a| * |b|) 编辑距离，空间 O(min(|a|,|b|))。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for i, cb in enumerate(b, 1):
        curr = [i] + [0] * len(a)
        for j, ca in enumerate(a, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def levenshtein_ratio(a: str, b: str) -> float:
    """Levenshtein 距离标准化为 [0,1]（0=完全不同，1=完全相同）。"""
    if not a and not b:
        return 1.0
    d = levenshtein_distance(a, b)
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    return 1.0 - (d / max_len)


def jaccard_similarity(
    set_a: set[Any] | list[Any] | None,
    set_b: set[Any] | list[Any] | None,
) -> float:
    """Jaccard 相似度：|A ∩ B| / |A ∪ B|。空集对空集定义为 1.0。"""
    sa = set(set_a or [])
    sb = set(set_b or [])
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def conclusion_consistency(
    runs: list[dict[str, Any]],
    conclusion_fields: tuple[str, ...],
) -> float:
    """结论稳定率：N 次运行里全部 conclusion_fields 值完全一致的条目 / 总条目。

    runs 是 N 个 ``{item_id: {field: value}}`` 字典。每次运行必须包含相同的 item_id
    集合。返回比例 [0, 1]。
    """
    if not runs:
        return 1.0
    item_ids = set(runs[0].keys())
    if not item_ids:
        return 1.0
    consistent = 0
    for item_id in item_ids:
        first = runs[0].get(item_id, {})
        all_same = True
        for field_name in conclusion_fields:
            baseline = first.get(field_name)
            for r in runs[1:]:
                if r.get(item_id, {}).get(field_name) != baseline:
                    all_same = False
                    break
            if not all_same:
                break
        if all_same:
            consistent += 1
    return consistent / len(item_ids)


def failure_state_consistency(runs: list[dict[str, Any]]) -> float:
    """failure_state 字段单独的一致率。"""
    return conclusion_consistency(runs, ("failure_state",))


def evidence_jaccard_mean(
    runs: list[dict[str, Any]],
    evidence_fields: tuple[str, ...],
) -> float:
    """对每条 item，对 N 次运行两两计算 Jaccard（evidence 字段均视为集合），取平均。

    返回所有 item 的平均 Jaccard，范围 [0,1]。
    """
    if not runs or len(runs) < 2:
        return 1.0
    item_ids = set(runs[0].keys())
    if not item_ids:
        return 1.0

    per_item_means: list[float] = []
    for item_id in item_ids:
        pairs_jaccards: list[float] = []
        for i in range(len(runs)):
            for j in range(i + 1, len(runs)):
                ri = runs[i].get(item_id, {})
                rj = runs[j].get(item_id, {})
                field_jaccards: list[float] = []
                for field_name in evidence_fields:
                    field_jaccards.append(
                        jaccard_similarity(ri.get(field_name), rj.get(field_name))
                    )
                if field_jaccards:
                    pairs_jaccards.append(sum(field_jaccards) / len(field_jaccards))
        if pairs_jaccards:
            per_item_means.append(sum(pairs_jaccards) / len(pairs_jaccards))
    return sum(per_item_means) / len(per_item_means) if per_item_means else 1.0


def explanation_drift_rate(
    runs: list[dict[str, Any]],
    conclusion_fields: tuple[str, ...],
    explanation_field: str,
    drift_threshold: float = 0.3,
) -> float:
    """解释漂移率：结论字段一致但 explanation 编辑距离 > 阈值（相似度 < 1-阈值）的比例。

    阈值 0.3 意味着 levenshtein_ratio < 0.7 视为漂移。
    """
    if not runs or len(runs) < 2:
        return 0.0
    item_ids = set(runs[0].keys())
    if not item_ids:
        return 0.0

    total_pairs = 0
    drifted_pairs = 0
    for item_id in item_ids:
        for i in range(len(runs)):
            for j in range(i + 1, len(runs)):
                ri = runs[i].get(item_id, {})
                rj = runs[j].get(item_id, {})
                # 结论字段完全一致才评估 explanation 漂移
                conclusion_same = all(
                    ri.get(f) == rj.get(f) for f in conclusion_fields
                )
                if not conclusion_same:
                    continue
                total_pairs += 1
                ratio = levenshtein_ratio(
                    str(ri.get(explanation_field, "")),
                    str(rj.get(explanation_field, "")),
                )
                if ratio < (1.0 - drift_threshold):
                    drifted_pairs += 1
    return drifted_pairs / total_pairs if total_pairs > 0 else 0.0


@dataclass
class StabilityMetrics:
    """四维稳定性指标结果。"""
    conclusion_rate: float
    failure_state_rate: float
    evidence_jaccard: float
    explanation_drift_rate: float
    total_items: int = 0
    total_runs: int = 0
    flow: str = ""
    project_id: int | str | None = None


class StabilityMetricsCalculator:
    """按流程计算四维指标的小计算器（纯函数）。"""

    # flow → (conclusion_fields, evidence_fields, explanation_field)
    _FLOW_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
        "investigate": (
            ("risk_level", "compliance_level", "is_compliant"),
            ("source_pages",),
            "comment",
        ),
        "collusion": (
            ("overall_risk",),
            ("findings_pages", "evidence_chunks"),
            "synthesis",
        ),
        "writer": (
            ("failure_state", "word_count_bucket"),
            ("source_material_ids", "source_clause_ids"),
            "content",
        ),
    }

    def compute(
        self,
        runs: list[dict[str, Any]],
        flow: str,
        project_id: int | str | None = None,
        drift_threshold: float = 0.3,
    ) -> StabilityMetrics:
        conclusion_fields, evidence_fields, explanation_field = \
            self._FLOW_FIELDS.get(flow, (("risk_level",), ("source_pages",), "comment"))
        return StabilityMetrics(
            conclusion_rate=conclusion_consistency(runs, conclusion_fields),
            failure_state_rate=failure_state_consistency(runs),
            evidence_jaccard=evidence_jaccard_mean(runs, evidence_fields),
            explanation_drift_rate=explanation_drift_rate(
                runs, conclusion_fields, explanation_field, drift_threshold,
            ),
            total_items=len(runs[0]) if runs else 0,
            total_runs=len(runs),
            flow=flow,
            project_id=project_id,
        )


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Stability Replay Report - {flow} / project {project_id}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 24px; }}
h1 {{ margin-top: 0; }}
table {{ border-collapse: collapse; margin: 16px 0; }}
th, td {{ border: 1px solid #ccc; padding: 6px 12px; }}
th {{ background: #f6f6f6; text-align: left; }}
.pass {{ color: #0a7a0a; }}
.warn {{ color: #c77300; }}
.fail {{ color: #b00020; }}
.small {{ color: #888; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>Stability Replay Report</h1>
<p class="small">flow = <b>{flow}</b> | project_id = <b>{project_id}</b> | runs = <b>{runs}</b> | items = <b>{items}</b></p>
<h2>Four-Dimension Metrics</h2>
<table>
<tr><th>指标</th><th>值</th><th>Lite 候选口径</th><th>判定</th></tr>
<tr><td>结论稳定率 conclusion_rate</td><td>{conclusion_rate:.4f}</td><td>>= 0.90</td><td class="{conclusion_class}">{conclusion_verdict}</td></tr>
<tr><td>失败合同稳定率 failure_state_rate</td><td>{failure_state_rate:.4f}</td><td>>= 0.95</td><td class="{failure_state_class}">{failure_state_verdict}</td></tr>
<tr><td>关键证据 Jaccard evidence_jaccard</td><td>{evidence_jaccard:.4f}</td><td>>= 0.85</td><td class="{evidence_class}">{evidence_verdict}</td></tr>
<tr><td>解释漂移率 explanation_drift_rate</td><td>{explanation_drift_rate:.4f}</td><td>&lt;= 0.25</td><td class="{explanation_class}">{explanation_verdict}</td></tr>
</table>
<p class="small">指标定义见 docs/regression_sample_spec.md §6.4；生产验收目标为 Lite 候选口径的更严档。</p>
</body>
</html>
"""


def render_html(m: StabilityMetrics) -> str:
    def _v(actual: float, threshold: float, higher_is_better: bool) -> tuple[str, str]:
        good = (actual >= threshold) if higher_is_better else (actual <= threshold)
        warn = (actual >= threshold * 0.95) if higher_is_better else (actual <= threshold * 1.05)
        if good:
            return "pass", "通过"
        if warn:
            return "warn", "接近阈值"
        return "fail", "不达标"

    c_cls, c_v = _v(m.conclusion_rate, 0.90, True)
    f_cls, f_v = _v(m.failure_state_rate, 0.95, True)
    e_cls, e_v = _v(m.evidence_jaccard, 0.85, True)
    x_cls, x_v = _v(m.explanation_drift_rate, 0.25, False)

    return _HTML_TEMPLATE.format(
        flow=m.flow,
        project_id=m.project_id if m.project_id is not None else "-",
        runs=m.total_runs,
        items=m.total_items,
        conclusion_rate=m.conclusion_rate,
        failure_state_rate=m.failure_state_rate,
        evidence_jaccard=m.evidence_jaccard,
        explanation_drift_rate=m.explanation_drift_rate,
        conclusion_class=c_cls, conclusion_verdict=c_v,
        failure_state_class=f_cls, failure_state_verdict=f_v,
        evidence_class=e_cls, evidence_verdict=e_v,
        explanation_class=x_cls, explanation_verdict=x_v,
    )


def emit_metrics_to_prometheus(m: StabilityMetrics) -> None:
    """把结果写入 observability.metrics 的 4 个 Gauge。失败静默。"""
    try:
        from backend.app.core.observability.metrics import (
            stability_conclusion_rate,
            stability_failure_state_rate,
            stability_evidence_jaccard,
            stability_explanation_drift_rate,
        )
        labels = dict(flow=m.flow, project_id=str(m.project_id or "unknown"))
        stability_conclusion_rate.labels(**labels).set(m.conclusion_rate)
        stability_failure_state_rate.labels(**labels).set(m.failure_state_rate)
        stability_evidence_jaccard.labels(**labels).set(m.evidence_jaccard)
        stability_explanation_drift_rate.labels(**labels).set(m.explanation_drift_rate)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_runs_from_json_dir(dir_path: Path) -> list[dict[str, Any]]:
    """从目录读取 run_*.json 文件作为 N 次回放输入。每个文件内容是 ``{item_id: {field: value, ...}, ...}``。"""
    runs: list[dict[str, Any]] = []
    files = sorted(dir_path.glob("run_*.json"))
    if not files:
        raise FileNotFoundError(f"no run_*.json files in {dir_path}")
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            runs.append(json.load(fh))
    return runs


def _run_live(project_id: int, flow: str, n: int) -> list[dict[str, Any]]:
    """真实调用 orchestrator 跑 N 次。

    当前 Lite 环境未具备一键回放能力（orchestrator 需要完整 ARQ + DB + 招标材料上下文），
    此函数抛 NotImplementedError 明确边界；P2+ 或生产上线后由专项任务实现。
    """
    raise NotImplementedError(
        "live 模式需要完整 ARQ + DB 上下文；Lite 环境请使用 --mode replay-json "
        "目录方式预先准备 run_*.json"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stability Replay Tool (S.4.2)")
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--flow", choices=["investigate", "collusion", "writer"], required=True)
    parser.add_argument("--runs", type=int, default=5, help="N 次回放")
    parser.add_argument("--output", type=Path, default=Path("stability_report.html"))
    parser.add_argument(
        "--mode", choices=["live", "replay-json"], default="replay-json",
    )
    parser.add_argument(
        "--replay-dir", type=Path, default=None,
        help="replay-json 模式的输入目录；包含 run_*.json 文件",
    )
    parser.add_argument(
        "--drift-threshold", type=float, default=0.3,
        help="解释漂移阈值（1 - levenshtein_ratio 大于该值即认为漂移）",
    )
    args = parser.parse_args(argv)

    if args.mode == "live":
        runs = _run_live(args.project_id, args.flow, args.runs)
    else:
        if args.replay_dir is None:
            args.replay_dir = Path(f"./samples/project_{args.project_id}/{args.flow}")
        runs = _load_runs_from_json_dir(args.replay_dir)
        if len(runs) < args.runs:
            print(
                f"warn: 目录只有 {len(runs)} 个 run 文件，小于请求的 {args.runs}；按实际可用数计算",
                file=sys.stderr,
            )

    calc = StabilityMetricsCalculator()
    m = calc.compute(
        runs, flow=args.flow, project_id=args.project_id,
        drift_threshold=args.drift_threshold,
    )

    emit_metrics_to_prometheus(m)

    html = render_html(m)
    args.output.write_text(html, encoding="utf-8")
    print(f"report written: {args.output}")
    print(
        f"  conclusion_rate={m.conclusion_rate:.4f} "
        f"failure_state_rate={m.failure_state_rate:.4f} "
        f"evidence_jaccard={m.evidence_jaccard:.4f} "
        f"explanation_drift_rate={m.explanation_drift_rate:.4f} "
        f"(items={m.total_items}, runs={m.total_runs})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
