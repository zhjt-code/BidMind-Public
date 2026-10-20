"""写标素材分块规划契约测试。"""

from __future__ import annotations
from tests.synthetic_data import synthetic_text

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.image_extractor import ExtractedImage  # noqa: E402
from app.core.parser.pdf_parser import ParsedPage  # noqa: E402
from app.core.parser.table_extractor import ExtractedTable  # noqa: E402
from app.core.writer.material_indexer import MaterialIndexer  # noqa: E402


def test_material_indexer_preserves_pdf_tables_from_parsed_pages() -> None:
    indexer = MaterialIndexer(db=None)  # type: ignore[arg-type]
    table = ExtractedTable(
        table_index=0,
        page_num=2,
        data=[
            ["姓名", "岗位", "社保月份"],
            ['<PERSON_A>', "项目经理", "2025-10至2026-03"],
        ],
        row_count=2,
        col_count=3,
    )
    page = ParsedPage(page_num=2, text="项目管理人员社保明细表", tables=[table])

    chunks = indexer.plan_material_chunks(content=page.text, page_texts=[page])

    assert any(
        chunk.metadata.get("chunk_type") == "table_row_with_header"
        and '姓名=<PERSON_A>' in chunk.content
        and "社保月份=2025-10至2026-03" in chunk.content
        for chunk in chunks
    )


def test_material_indexer_adds_independent_image_evidence_chunks() -> None:
    indexer = MaterialIndexer(db=None)  # type: ignore[arg-type]
    image = ExtractedImage(
        image_index=0,
        page_num=3,
        width=900,
        height=1200,
        image_format="jpeg",
        xref=1,
        spatial_context="营业执照附件",
        visual_content=synthetic_text('营业执照扫描件：', '统一社会信用代码 ', '91330100M', 'A8888888X', '。'),
    )
    page = ParsedPage(page_num=3, text="资格证明材料\n详见营业执照附件。", images=[image])

    chunks = indexer.plan_material_chunks(content=page.text, page_texts=[page])

    assert any(
        chunk.metadata.get("chunk_type") == "image_evidence"
        and synthetic_text('913301', '00MA88', '88888X') in chunk.content
        for chunk in chunks
    )
