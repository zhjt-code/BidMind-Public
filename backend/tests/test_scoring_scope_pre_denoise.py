# -*- coding: utf-8 -*-
"""评分作用域标题跨生产降噪链防御测试。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.ledger.extractor_bridge import _chunk_inputs  # noqa: E402
from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk  # noqa: E402
from app.core.parser import ChunkPlanner  # noqa: E402
from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # noqa: E402
from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks  # noqa: E402
from app.core.parser.text_cleaner import denoise_pages  # noqa: E402

pytestmark = pytest.mark.scoring

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "scoring"
    / "sample_corpus_holdout"
    / "26.json"
)


def _document() -> ParsedDocument:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def table(raw: object, index: int, page_num: int) -> SimpleNamespace:
        if isinstance(raw, dict):
            return SimpleNamespace(
                data=raw.get("data") or [],
                cell_grid=raw.get("cell_grid"),
                table_index=index,
                page_num=page_num,
            )
        return SimpleNamespace(
            data=raw, cell_grid=None, table_index=index, page_num=page_num
        )

    pages = [
        ParsedPage(
            page_num=page["page_num"],
            text=page.get("text") or "",
            tables=[
                table(raw, index, page["page_num"])
                for index, raw in enumerate(page.get("tables") or [])
            ],
        )
        for page in fixture["pages"]
    ]
    return ParsedDocument(
        file_path=fixture["source"],
        filename=fixture["source"],
        page_count=fixture["page_count"],
        pages=pages,
    )


def test_scope_titles_use_pre_denoise_snapshot_in_production_order() -> None:
    """生产顺序是先降噪再装配；标题定位必须显式消费降噪前快照。"""
    document = _document()
    raw_page_texts = {
        page.page_num: str(page.text or "") for page in document.pages
    }
    denoise_pages(document.pages)
    denoised_lines = {
        line.strip()
        for page in document.pages
        for line in str(page.text or "").splitlines()
    }
    assert "5.2.1 商务评审" not in denoised_lines
    assert "5.2.2 技术评审" not in denoised_lines

    chunks = build_scoring_table_chunks(
        document,
        ChunkPlanner(),
        0,
        scope_page_texts=raw_page_texts,
    )
    combined = next(
        chunk
        for chunk in chunks
        if len(((payload_from_chunk(chunk) or {}).get("grid") or [])) == 34
    )
    scope_rows = (payload_from_chunk(combined) or {}).get("scope_rows")

    assert scope_rows == [
        {"start": 0, "end": 10, "label": "商务评审", "declared": 100.0},
        {"start": 11, "end": 33, "label": "技术评审", "declared": 100.0},
    ]
    _pages, _grids, parallel_labels = _chunk_inputs([combined])
    assert parallel_labels == ["商务评审", "技术评审"]
