"""A/B 回归执行器：加载样本、调度运行、汇总结果。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SAMPLE_BASE = Path(__file__).resolve().parent.parent.parent / "tests" / "regression"


def _read_meta_source(proj_dir: Path) -> str:
    """读取 meta.yml 中的 source 字段（不引入 yaml 依赖，手工解析最小集）。"""
    meta_file = proj_dir / "meta.yml"
    if not meta_file.exists():
        return ""
    try:
        for line in meta_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("source:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def discover_samples(
    flow: str,
    level: str,
    allow_synthetic: bool = False,
) -> list[dict[str, Any]]:
    """发现指定 flow + level 下的所有样本。

    Parameters
    ----------
    flow : str
        ``investigate`` / ``collusion`` / ``writer`` / ``all``
    level : str
        ``1`` / ``2`` / ``3`` / ``all``
    allow_synthetic : bool
        是否纳入 meta.yml 标注为 ``synthetic_*`` 的样本。
        默认 False（防止合成样本污染 A/B 基础）。

    Returns
    -------
    list[dict]
        每项包含 ``flow``, ``level``, ``project_dir``, ``expected_path``, ``source``。
    """
    flows = ["investigate", "collusion", "writer"] if flow == "all" else [flow]
    level_map = {"1": "level1_historical", "2": "level2_attack", "3": "level3_synthetic"}
    levels = list(level_map.values()) if level == "all" else [level_map[level]]

    samples: list[dict[str, Any]] = []
    skipped_synthetic = 0
    for f in flows:
        for lv in levels:
            base_dir = SAMPLE_BASE / f / lv
            if not base_dir.exists():
                continue
            for proj_dir in sorted(base_dir.iterdir()):
                if not proj_dir.is_dir():
                    continue
                expected_file = proj_dir / "expected.jsonl"
                if not expected_file.exists():
                    continue
                source = _read_meta_source(proj_dir)
                is_synthetic = source.startswith("synthetic")
                if is_synthetic and not allow_synthetic:
                    skipped_synthetic += 1
                    continue
                samples.append({
                    "flow": f,
                    "level": lv,
                    "project_dir": str(proj_dir),
                    "expected_path": str(expected_file),
                    "source": source,
                })
    if skipped_synthetic:
        print(f"[regression_run] Skipped {skipped_synthetic} synthetic samples "
              "(use --allow-synthetic to include; see tests/regression/SYNTHETIC_NOTICE.md)")
    return samples


def load_expected(expected_path: str) -> list[dict[str, Any]]:
    """加载 expected.jsonl 文件。"""
    results: list[dict[str, Any]] = []
    with open(expected_path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def run_ab_comparison(
    flow: str,
    level: str,
    baseline_branch: str,
    candidate_branch: str,
    allow_synthetic: bool = False,
    baseline_snapshot: str | None = None,
    candidate_snapshot: str | None = None,
) -> dict[str, Any]:
    """执行 A/B 对比。

    当前 B0 阶段为"自洽模式"：baseline == candidate == main，
    直接加载样本的 expected.jsonl 作为两侧结果进行 diff。
    真正的 LLM re-run 模式将在 B1 后可用。

    Returns
    -------
    dict
        包含 ``samples``, ``baseline_results``, ``candidate_results``,
        ``diff_summary``, ``redline_report``, ``has_synthetic`` 字段。
    """
    from .diff import compute_field_diffs, DiffSummary
    from .redlines import (
        check_investigate_redlines,
        check_collusion_redlines,
        check_writer_redlines,
        RedlineReport,
    )
    if flow == "writer" and (baseline_snapshot or candidate_snapshot):
        if not baseline_snapshot or not candidate_snapshot:
            raise ValueError("two distinct writer snapshots are required")
        from .writer_quality import compare_writer_quality_snapshots

        return compare_writer_quality_snapshots(
            baseline_snapshot,
            candidate_snapshot,
        )

    samples = discover_samples(flow, level, allow_synthetic=allow_synthetic)
    has_synthetic = any(s.get("source", "").startswith("synthetic") for s in samples)

    all_baseline: list[dict] = []
    all_candidate: list[dict] = []

    for s in samples:
        expected = load_expected(s["expected_path"])
        all_baseline.extend(expected)
        all_candidate.extend(expected)

    key_field = "requirement_id" if flow == "investigate" else "pair_id" if flow == "collusion" else "outline_node_id"
    diff_summary = compute_field_diffs(all_baseline, all_candidate, key_field=key_field)

    redline_report = RedlineReport()
    if flow in ("investigate", "all"):
        redline_report.results.extend(check_investigate_redlines(all_baseline, all_candidate))
    if flow in ("collusion", "all"):
        redline_report.results.extend(check_collusion_redlines(all_baseline, all_candidate))
    if flow in ("writer", "all"):
        redline_report.results.extend(check_writer_redlines(all_baseline, all_candidate))

    return {
        "flow": flow,
        "level": level,
        "baseline_branch": baseline_branch,
        "candidate_branch": candidate_branch,
        "sample_count": len(samples),
        "record_count": len(all_baseline),
        "diff_summary": diff_summary,
        "redline_report": redline_report,
        "has_synthetic": has_synthetic,
        "allow_synthetic": allow_synthetic,
    }
