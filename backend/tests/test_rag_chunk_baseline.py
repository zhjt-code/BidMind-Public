"""RAG 分块离线基准测试。"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.rag_chunk_baseline import run_baseline  # noqa: E402


def test_rag_chunk_baseline_scores_are_stable() -> None:
    result = run_baseline()
    summary = result["summary"]

    assert summary["structured_main"]["scenario_count"] == 8
    assert summary["tuple_compat"]["scenario_count"] == 8
    assert summary["material_indexer"]["scenario_count"] == 8
    assert summary["structured_main"]["score"] >= 0.95
    assert summary["tuple_compat"]["score"] >= 0.70
    assert summary["material_indexer"]["score"] >= 0.95


def test_structured_profile_outperforms_tuple_compat() -> None:
    result = run_baseline()
    summary = result["summary"]

    assert summary["structured_main"]["score"] > summary["tuple_compat"]["score"]
    assert summary["material_indexer"]["score"] > summary["tuple_compat"]["score"]
