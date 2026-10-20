import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.modules.pop("regression", None)


def _snapshot(path: Path, digest: str, *, gate: str = "observed") -> Path:
    payload = {
        "schema_version": "writer_quality_snapshot.v1",
        "sample_set_digest": digest,
        "project_count": 1,
        "section_count": 2,
        "placeholder_count": 1,
        "high_issue_count": 0,
        "fixed_template_high_issue_count": 0,
        "drafts_without_material_sources": 0,
        "failed_drafts": 0,
        "needs_review_drafts": 0,
        "gate_signal_availability": gate,
        "judge_signal_availability": "unavailable",
        "cove_signal_availability": "partial_nonpass_only",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_writer_quality_comparison_rejects_mismatched_sample_sets(tmp_path: Path) -> None:
    from regression.writer_quality import compare_writer_quality_snapshots

    baseline = _snapshot(tmp_path / "baseline.json", "a" * 64)
    candidate = _snapshot(tmp_path / "candidate.json", "b" * 64)

    with pytest.raises(ValueError, match="sample_set_digest"):
        compare_writer_quality_snapshots(baseline, candidate)


def test_writer_quality_comparison_flags_signal_coverage_regression(tmp_path: Path) -> None:
    from regression.writer_quality import compare_writer_quality_snapshots

    baseline = _snapshot(tmp_path / "baseline.json", "a" * 64, gate="observed")
    candidate = _snapshot(tmp_path / "candidate.json", "a" * 64, gate="unavailable")

    result = compare_writer_quality_snapshots(baseline, candidate)

    assert result["redline_report"].failed_count == 1
    assert result["redline_report"].results[0].name == "writer质量信号覆盖退化"


def test_writer_quality_comparison_does_not_treat_clean_gate_as_regression(tmp_path: Path) -> None:
    from regression.writer_quality import compare_writer_quality_snapshots

    baseline = _snapshot(tmp_path / "baseline.json", "a" * 64, gate="partial_nonpass_only")
    candidate = _snapshot(tmp_path / "candidate.json", "a" * 64, gate="unavailable")

    result = compare_writer_quality_snapshots(baseline, candidate)

    assert result["redline_report"].failed_count == 0


def test_writer_run_ab_requires_two_distinct_snapshots_for_writer(tmp_path: Path) -> None:
    from regression.runner import run_ab_comparison

    baseline = _snapshot(tmp_path / "same.json", "a" * 64)

    with pytest.raises(ValueError, match="two distinct writer snapshots"):
        run_ab_comparison(
            flow="writer",
            level="1",
            baseline_branch="main",
            candidate_branch="feature",
            baseline_snapshot=str(baseline),
            candidate_snapshot=str(baseline),
        )
