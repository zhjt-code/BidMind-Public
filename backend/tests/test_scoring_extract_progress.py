""": 评分 chunk 遍历把完成数回传给 on_chunk_done。

不在本文件 import ScoringExtractor: 同进程若先收集
test_tender_extractor_merge_progress_label, 会把 app.core.ai.prompts
替换成无 scoring_extract 子模块的假包。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_pipeline_core import (  # noqa: E402
    as_extraction_chunk,
    run_chunk_extraction_pass,
)
from app.core.extractor.scoring.scoring_progress import emit_scoring_progress  # noqa: E402


pytestmark = pytest.mark.scoring


@pytest.mark.asyncio
async def test_chunk_pass_notifies_on_each_chunk() -> None:
    raws = [
        SimpleNamespace(
            content=f"[评分表 第{idx}-1页]\n| 序号 | 评分项目 |\n| 1 | 演示 |",
            metadata={"page_num": idx, "section_path": "评标办法"},
            chunk_index=idx,
            chunk_metadata={},
        )
        for idx in (1, 2)
    ]
    chunks = [as_extraction_chunk(raw, chunk_id=idx) for idx, raw in enumerate(raws, start=1)]
    seen: list[tuple[int, int]] = []

    async def _llm_fallback(_c):
        return [], {}

    await run_chunk_extraction_pass(
        chunks,
        llm_fallback=_llm_fallback,
        dedupe_exact=lambda items: list(items),
        absorb=lambda survivors, incoming: survivors + incoming,
        document_id=1,
        on_chunk_done=lambda done, total: seen.append((done, total)),
    )

    assert seen == [(1, 2), (2, 2)]


def test_chunk_callback_maps_to_scoring_extract_stage() -> None:
    """chunk 完成数经折算后 stage 恒为 scoring_extract, current 递增。"""
    seen: list[tuple[str, int]] = []

    def spy(stage: str, current: int, total: int, message: str) -> None:
        seen.append((stage, current))

    emit_scoring_progress(spy, "chunks", 1, 4, "解析评分表 1/4")
    emit_scoring_progress(spy, "chunks", 2, 4, "解析评分表 2/4")

    assert [item[0] for item in seen] == ["scoring_extract", "scoring_extract"]
    assert seen[0][1] < seen[1][1]
