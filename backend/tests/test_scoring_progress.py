""": 评分子阶段计数折算为 scoring_extract current/total。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_progress import (  # noqa: E402
    emit_scoring_progress,
    map_scoring_phase,
)

import pytest

pytestmark = pytest.mark.scoring


def test_map_scoring_phase_chunks_quarter() -> None:
    current, total, detail = map_scoring_phase("chunks", 1, 4, "解析评分表 1/4")
    assert total == 100
    assert current == 15
    assert detail == "解析评分表 1/4"


def test_map_scoring_phase_l2_complete_is_span_end() -> None:
    current, total, _ = map_scoring_phase("l2_repair", 1, 1, "评分项修补完成")
    assert total == 100
    assert current == 75


def test_map_scoring_phase_zero_total_uses_span_end() -> None:
    current, total, _ = map_scoring_phase("chunks", 0, 0, "无评分表")
    assert total == 100
    assert current == 60


def test_emit_scoring_progress_noop_without_callback() -> None:
    emit_scoring_progress(None, "chunks", 1, 2, "x")


def test_emit_scoring_progress_forwards_mapped_args() -> None:
    seen: list[tuple] = []

    def spy(stage: str, current: int, total: int, message: str) -> None:
        seen.append((stage, current, total, message))

    emit_scoring_progress(spy, "vlm", 0, 1, "VLM视觉救援中")
    assert seen == [("scoring_extract", 90, 100, "VLM视觉救援中")]


def test_pdf_chat_phase_occupies_rescue_first_half() -> None:
    """: PDF 直读占用评分步内 85-90, 不得再挤进 vlm 段。"""
    start, total, detail = map_scoring_phase("pdf_chat", 0, 1, "PDF直读救援中")
    done, _, done_detail = map_scoring_phase("pdf_chat", 1, 1, "PDF直读救援完成")
    assert (start, total, detail) == (85, 100, "PDF直读救援中")
    assert (done, done_detail) == (90, "PDF直读救援完成")


def test_vlm_phase_occupies_rescue_second_half() -> None:
    start, _, _ = map_scoring_phase("vlm", 0, 1, "VLM视觉救援中")
    current, total, _ = map_scoring_phase("vlm", 1, 1, "VLM视觉救援完成")
    assert start == 90
    assert total == 100
    assert current == 95


def test_emit_persist_phase_advances_to_hundred() -> None:
    seen: list[int] = []

    def spy(stage: str, current: int, total: int, message: str) -> None:
        assert stage == "scoring_extract"
        seen.append(current)

    emit_scoring_progress(spy, "persist", 0, 1, "保存评分项")
    emit_scoring_progress(spy, "persist", 1, 1, "已保存 3 条评分项")
    assert seen == [95, 100]
