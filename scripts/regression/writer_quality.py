"""Writer quality snapshot comparison."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .diff import DiffSummary, compute_field_diffs
from .redlines import RedlineReport, RedlineResult

_AVAILABILITY_RANK = {
    "unavailable": 0,
    "partial_nonpass_only": 0,
    "observed": 1,
}


def _load_snapshot(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "writer_quality_snapshot.v1":
        raise ValueError("unsupported writer quality snapshot schema_version")
    if not data.get("sample_set_digest"):
        raise ValueError("writer quality snapshot missing sample_set_digest")
    return data


def _check_signal_coverage(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[RedlineResult]:
    fields = [
        "gate_signal_availability",
        "judge_signal_availability",
        "cove_signal_availability",
    ]
    failed: list[str] = []
    for field in fields:
        base_rank = _AVAILABILITY_RANK.get(str(baseline.get(field)), -1)
        candidate_rank = _AVAILABILITY_RANK.get(str(candidate.get(field)), -1)
        if candidate_rank < base_rank:
            failed.append(field)
    return [
        RedlineResult(
            name="writer质量信号覆盖退化",
            flow="writer",
            passed=not failed,
            baseline_value={
                field: baseline.get(field)
                for field in fields
            },
            candidate_value={
                field: candidate.get(field)
                for field in fields
            },
            threshold="candidate availability must not regress",
            detail=";".join(failed),
        )
    ]


def compare_writer_quality_snapshots(
    baseline_snapshot: str | Path,
    candidate_snapshot: str | Path,
) -> dict[str, Any]:
    """Compare two independent no-source-text writer quality snapshots."""

    baseline_path = Path(baseline_snapshot)
    candidate_path = Path(candidate_snapshot)
    if baseline_path.resolve() == candidate_path.resolve():
        raise ValueError("two distinct writer snapshots are required")

    baseline = _load_snapshot(baseline_path)
    candidate = _load_snapshot(candidate_path)
    if baseline["sample_set_digest"] != candidate["sample_set_digest"]:
        raise ValueError("sample_set_digest mismatch")

    diff_summary = compute_field_diffs(
        [{"snapshot_id": "writer_quality", **baseline}],
        [{"snapshot_id": "writer_quality", **candidate}],
        key_field="snapshot_id",
    )
    redline_report = RedlineReport(
        results=_check_signal_coverage(baseline, candidate)
    )
    return {
        "flow": "writer",
        "level": "quality",
        "baseline_branch": str(baseline_path),
        "candidate_branch": str(candidate_path),
        "sample_count": 1,
        "record_count": 1,
        "diff_summary": diff_summary,
        "redline_report": redline_report,
        "has_synthetic": False,
        "allow_synthetic": False,
        "baseline_results": [baseline],
        "candidate_results": [candidate],
    }


__all__ = ["compare_writer_quality_snapshots"]
