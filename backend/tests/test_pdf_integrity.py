"""PDF解析产物完整性闸门与内容指纹防御测试（综合提升方案 阶段0.3, 缺口D）。

职责: 锁定"损坏/空壳文档 fail-visible"判定与逻辑副本指纹的关键行为。
不做: 不测上传路由、不测数据库迁移(人工验证步骤覆盖)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.pdf_integrity import (  # noqa: E402
    assess_parsed_document_integrity,
    normalized_content_hash,
)


def _doc(pages: list, page_count: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(pages=pages, page_count=page_count or len(pages))


def _page(text: str = "", tables: list | None = None, images: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(text=text, tables=tables or [], images=images or [])


def test_zero_pages_is_unusable() -> None:
    report = assess_parsed_document_integrity(_doc([], page_count=0))
    assert not report.usable
    assert "无可渲染页面" in report.reason


def test_empty_shell_pages_are_unusable() -> None:
    report = assess_parsed_document_integrity(_doc([_page(), _page()]))
    assert not report.usable
    assert "无文本、表格与图片" in report.reason


def test_scan_only_document_is_usable() -> None:
    # 扫描件: 无文本但有图片, 必须放行交 OCR/VL 通道, 不得误杀
    report = assess_parsed_document_integrity(_doc([_page(images=[object()])]))
    assert report.usable


def test_table_only_document_is_usable() -> None:
    report = assess_parsed_document_integrity(_doc([_page(tables=[object()])]))
    assert report.usable


def test_normal_text_document_is_usable() -> None:
    report = assess_parsed_document_integrity(_doc([_page(text="评分办法 第三章")]))
    assert report.usable
    assert report.char_count > 0


def test_content_hash_is_whitespace_insensitive() -> None:
    # 逻辑副本(样本19≈21): 同一内容不同排版/换行应产生相同指纹
    a = _doc([_page(text="评分因素 分值\n商务部分：10分")])
    b = _doc([_page(text="评分因素分值商务部分：10分")])
    assert normalized_content_hash(a) == normalized_content_hash(b)


def test_content_hash_differs_for_different_content() -> None:
    a = _doc([_page(text="商务部分：10分")])
    b = _doc([_page(text="商务部分：20分")])
    assert normalized_content_hash(a) != normalized_content_hash(b)


def test_content_hash_empty_document_returns_none() -> None:
    assert normalized_content_hash(_doc([_page()])) is None
