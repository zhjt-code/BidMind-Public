""" 防御测试: 采购包/标段评分边界定位。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.parser.lot_boundary_locator import locate_lot_boundaries  # noqa: E402
from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # noqa: E402

pytestmark = pytest.mark.scoring


def _doc(pages: list[ParsedPage]) -> ParsedDocument:
    return ParsedDocument(
        file_path="x.pdf",
        filename="x.pdf",
        page_count=max(p.page_num for p in pages),
        pages=pages,
    )


def test_locates_two_package_scoring_boundaries_1552():
    pages = [
        ParsedPage(page_num=31, text="符合性审查：采购包1：若违反条款无效。采购包2：若违反条款无效。"),
        ParsedPage(
            page_num=34,
            text=(
                "7、评标方法和标准7.1评标方法: 采购包1:综合评分法 7.2评标标准采购包1:综合评分法 "
                "每个投标人的评标总得分FA=F1×A1+F2×A2+F3×A3。"
            ),
        ),
        ParsedPage(
            page_num=40,
            text=(
                "中标候选人排列规则如下。采购包2:综合评分法 "
                "每个投标人的评标总得分FA=F1×A1+F2×A2+F3×A3。"
            ),
        ),
        ParsedPage(page_num=52, text="售后服务考核表。"),
    ]

    lots = locate_lot_boundaries(_doc(pages))

    assert [(lot.label, lot.page_start, lot.page_end) for lot in lots] == [
        ("采购包1", 34, 39),
        ("采购包2", 40, 52),
    ]


def test_same_page_package_names_without_scoring_context_are_not_anchors():
    pages = [
        ParsedPage(page_num=31, text="符合性审查：采购包1：若违反条款无效。采购包2：若违反条款无效。"),
        ParsedPage(page_num=32, text="本项目规定的其他情形，采购包1和采购包2均适用。"),
    ]

    assert locate_lot_boundaries(_doc(pages)) == []


def test_single_package_document_returns_empty_for_backward_compatibility():
    pages = [
        ParsedPage(page_num=10, text="评标办法：采购包1:综合评分法，每个投标人的评标总得分FA=100分。"),
        ParsedPage(page_num=11, text="评分标准如下。"),
    ]

    assert locate_lot_boundaries(_doc(pages)) == []
