"""ChunkPlanner 测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.chunk_planner import ChunkPlanner
from app.core.parser.pdf_parser import ParsedPage
from app.core.parser.table_extractor import ExtractedTable


def test_chunk_planner_preserves_clause_numbering() -> None:
    planner = ChunkPlanner(max_item_chars=120)
    page = ParsedPage(
        page_num=1,
        text=(
            "第一章 资格要求\n\n"
            "3.2.1 投标人须具备建筑工程施工总承包二级及以上资质，并提供营业执照。"
        ),
    )

    chunks = planner.plan_parsed_pages([page])
    assert chunks
    assert any(chunk.metadata.get("clause_no") == "3.2.1" for chunk in chunks)
    assert all(chunk.metadata.get("section_path") for chunk in chunks)


def test_chunk_planner_keeps_table_header_and_row_relation() -> None:
    planner = ChunkPlanner(max_item_chars=200)
    table = ExtractedTable(
        table_index=0,
        page_num=2,
        data=[
            ["项目名称", "单位", "数量", "备注"],
            ["钢筋", "吨", "120", "HRB400"],
            ["混凝土", "方", "300", "C30"],
        ],
        row_count=3,
        col_count=4,
    )
    page = ParsedPage(page_num=2, text="技术参数表", tables=[table])

    chunks = planner.plan_parsed_pages([page])
    table_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "table_row_with_header"]
    assert len(table_chunks) == 2
    assert "项目名称=钢筋" in table_chunks[0].content
    assert "数量=120" in table_chunks[0].content


def test_chunk_planner_only_splits_long_text_on_safe_boundaries() -> None:
    planner = ChunkPlanner(max_item_chars=20)
    page = ParsedPage(
        page_num=3,
        text="3.5.1 投标人须提供营业执照；安全生产许可证；项目经理建造师证书；近三年类似项目业绩证明。",
    )

    chunks = planner.plan_parsed_pages([page])
    assert len(chunks) >= 2
    assert all(len(chunk.content) <= 20 for chunk in chunks)
    assert all(chunk.metadata.get("chunk_type") == "clause" for chunk in chunks)


def test_chunk_planner_label_value_grid_falls_back_to_structural_text() -> None:
    """: 标签-值网格(图4顶部)不应走 `表头=值` 错配, 应输出结构行文本."""
    planner = ChunkPlanner(max_item_chars=200)
    table = ExtractedTable(
        table_index=0,
        page_num=5,
        data=[
            ["姓名", '<PERSON_A>', "性别", "男", "年龄", "45"],
            ["在服务及以往服务项目情况", "", "", "", "", ""],
            ["职务", "总经理", "职称", "教授级高工", "学历", "博士"],
        ],
        row_count=3,
        col_count=6,
    )
    page = ParsedPage(page_num=5, text="项目经理简历表", tables=[table])

    chunks = planner.plan_parsed_pages([page])
    table_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table_row_with_header"]

    assert table_chunks, "应至少产出一条表格 chunk"
    contents = [c.content for c in table_chunks]
    # 错配防御断言: 旧实现会产出这两个串
    for c in contents:
        assert "姓名=在服务及以往服务项目情况" not in c
        assert "姓名=参加工作时间" not in c
    # 行内仍可被检索定位到关键字段
    assert any('姓名 | <PERSON_A>' in c for c in contents)
    assert any("在服务及以往服务项目情况" in c for c in contents)


def test_chunk_planner_shadow_compare_reports_diff() -> None:
    planner = ChunkPlanner(max_item_chars=80)
    page = ParsedPage(page_num=1, text="第一条 " + "测试内容；" * 50)

    summary = planner.shadow_compare([page])
    assert summary.old_count >= 1
    assert summary.new_count >= 1
    assert summary.new_max_chars <= summary.old_max_chars
