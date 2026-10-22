"""文档质检测试共享夹具: 纯内存 DocumentModel 构造器(不依赖 PDF/DB)"""

from __future__ import annotations

from typing import Optional

import pytest

from app.core.document_qa.contracts import (
    BBox,
    DocumentModel,
    QaHeading,
    QaLine,
    QaPage,
    QaRegion,
    QaSpan,
)
from app.core.document_qa.model_builder import build_paragraphs


def make_model(
    *,
    body_texts: dict[int, str],
    headings: Optional[list[QaHeading]] = None,
    document_id: int = 101,
    bid_part: Optional[str] = None,
    page_size: tuple[float, float] = (595.0, 842.0),
) -> DocumentModel:
    """按页文本构造最小 DocumentModel(段落由生产同款 build_paragraphs 重建)。"""
    pages = [
        QaPage(
            page_num=page_num,
            width=page_size[0],
            height=page_size[1],
            rotation=0,
            char_count=len(text),
            image_count=0,
            table_count=0,
        )
        for page_num, text in sorted(body_texts.items())
    ]
    paragraphs = {p: build_paragraphs(t) for p, t in body_texts.items()}
    return DocumentModel(
        document_id=document_id,
        bid_part=bid_part,
        filename="fixture.pdf",
        page_count=len(pages),
        pages=pages,
        body_texts=dict(body_texts),
        paragraphs=paragraphs,
        visual_paragraphs={p: list(items) for p, items in paragraphs.items()},
        headings=list(headings or []),
    )


def make_line(
    page: int,
    text: str,
    bbox: BBox,
    *,
    font: str = "SimSun",
    size: float = 12.0,
    color: int = 0,
    rotated: bool = False,
) -> QaLine:
    """一行文字(单个 span); 用于构造带版面区域的模型。"""
    span = QaSpan(page=page, text=text, bbox=bbox, font=font, size=size, flags=0, color=color)
    return QaLine(page=page, text=text, bbox=bbox, spans=(span,), rotated=rotated)


def make_region(
    page: int,
    kind: str,
    bbox: BBox,
    lines: list[QaLine],
    *,
    label: str = "",
    order: int = 0,
) -> QaRegion:
    """版面区域(kind 取 region_text.KIND_*)。"""
    return QaRegion(
        page=page, kind=kind, label=label or kind, bbox=bbox, order=order, lines=tuple(lines),
    )


def with_regions(model: DocumentModel, regions: dict[int, list[QaRegion]]) -> DocumentModel:
    """给模型挂上版面区域: 这些页走按区域取数, 其余页仍用旧来源。"""
    model.regions = {page: list(items) for page, items in regions.items()}
    return model


@pytest.fixture()
def qa_rules() -> dict:
    from app.core.document_qa.rules.loader import load_rules

    return load_rules()
