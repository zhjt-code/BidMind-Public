"""文档门禁可用性预检测试。"""

from __future__ import annotations

import io

import fitz
import pytest
from PIL import Image

from app.core.parser.doc_gate.usability import (
    UsabilityAssessment,
    assess_pdf_usability,
    build_usability_gate_result,
)


def _pdf_bytes_from_doc(doc: fitz.Document, **save_kwargs) -> bytes:
    buf = io.BytesIO()
    doc.save(buf, **save_kwargs)
    doc.close()
    return buf.getvalue()


def _empty_shell_pdf(pages: int = 2) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    return _pdf_bytes_from_doc(doc)


def _text_pdf(text: str = "hello tender") -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    return _pdf_bytes_from_doc(doc)


def _image_only_pdf() -> bytes:
    img = Image.new("RGB", (100, 100), (200, 30, 30))
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(40, 40, 140, 140), stream=png_buf.getvalue())
    return _pdf_bytes_from_doc(doc)


def _encrypted_pdf(user_pw: str = "secret") -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "secret content")
    return _pdf_bytes_from_doc(
        doc,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw=user_pw,
    )


def test_encrypted_pdf_is_encrypted():
    assessment = assess_pdf_usability(_encrypted_pdf())
    assert assessment.kind == "encrypted"
    result = build_usability_gate_result(assessment, "tender")
    assert result.verdict == "encrypted"
    assert result.engine == "usability"
    assert any("加密" in r for r in result.reasons)


def test_corrupt_bytes_are_corrupt():
    assessment = assess_pdf_usability(b"%PDF-not-a-real-file")
    assert assessment.kind == "corrupt"
    result = build_usability_gate_result(assessment, "bid")
    assert result.verdict == "corrupt"
    assert any("损坏" in r or "无法正常打开" in r for r in result.reasons)


def test_empty_shell_is_empty():
    assessment = assess_pdf_usability(_empty_shell_pdf())
    assert assessment.kind == "empty"
    assert assessment.page_count == 2
    result = build_usability_gate_result(assessment, "tender")
    assert result.verdict == "empty"
    assert any("无文本、表格与图片" in r for r in result.reasons)


def test_image_only_scan_is_usable_for_compliance_path():
    assessment = assess_pdf_usability(_image_only_pdf())
    assert assessment.kind is None
    assert assessment.page_count >= 1


def test_text_pdf_is_usable():
    assessment = assess_pdf_usability(_text_pdf())
    assert assessment.kind is None


def test_build_rejects_usable_assessment():
    with pytest.raises(ValueError):
        build_usability_gate_result(
            UsabilityAssessment(kind=None, page_count=1),
            "tender",
        )
