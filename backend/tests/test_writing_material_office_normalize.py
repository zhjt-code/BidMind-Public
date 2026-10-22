"""写标/知识库 Office→PDF 统一归一防御测试。

问题形态: 可转页级 PDF 的载体若直走弱抽取路径，内容识别与 PDF 主链分叉。
夹具: 写标 .docx/.doc；知识库 .docx；parse_only 拒绝未归一 Office。
"""

from __future__ import annotations

import os
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.convert.office_to_pdf import (  # noqa: E402
    converted_pdf_path,
    normalize_office_path_for_parse,
)
from app.core.knowledge.atomizer import KnowledgeAtomizer  # noqa: E402
from app.core.writer.material_content_extractor import MaterialContentExtractor  # noqa: E402
from app.services.writing.material_batch import phase1_parse  # noqa: E402


class _AsyncNoopDB:
    def __init__(self) -> None:
        self.commit_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1


@pytest.mark.asyncio
async def test_normalize_office_path_docx_and_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    docx = tmp_path / "业绩证明.docx"
    doc = tmp_path / "营业执照.doc"
    docx.write_bytes(b"PK\x03\x04dummy")
    doc.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"x")
    dest_docx = converted_pdf_path(docx)
    dest_doc = converted_pdf_path(doc)
    dest_docx.write_bytes(b"%PDF-1.4 a")
    dest_doc.write_bytes(b"%PDF-1.4 b")

    convert = AsyncMock(side_effect=[dest_docx, dest_doc])
    monkeypatch.setattr(
        "app.core.convert.office_to_pdf.convert_office_to_pdf", convert,
    )

    p1, e1 = await normalize_office_path_for_parse(docx)
    p2, e2 = await normalize_office_path_for_parse(doc)
    assert (p1, e1) == (dest_docx, ".pdf")
    assert (p2, e2) == (dest_doc, ".pdf")
    assert convert.await_count == 2


@pytest.mark.asyncio
async def test_normalize_material_source_rewrites_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "业绩证明.docx"
    src.write_bytes(b"PK\x03\x04dummy")
    dest = converted_pdf_path(src)
    dest.write_bytes(b"%PDF-1.4 converted")

    mat = types.SimpleNamespace(
        id=11,
        title="业绩证明.docx",
        file_path=str(src),
        processing_status="processing",
    )
    convert = AsyncMock(return_value=dest)
    monkeypatch.setattr(
        "app.core.convert.office_to_pdf.convert_office_to_pdf", convert,
    )

    ext = await phase1_parse.normalize_material_source_for_parse(mat)

    assert ext == ".pdf"
    assert mat.file_path == str(dest)
    assert mat.title == "业绩证明.docx"
    convert.assert_awaited_once()


@pytest.mark.asyncio
async def test_normalize_pdf_passthrough_no_convert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "native.pdf"
    src.write_bytes(b"%PDF-1.4 native")
    convert = AsyncMock()
    monkeypatch.setattr(
        "app.core.convert.office_to_pdf.convert_office_to_pdf", convert,
    )

    resolved, ext = await normalize_office_path_for_parse(src)
    assert resolved == src
    assert ext == ".pdf"
    convert.assert_not_awaited()


@pytest.mark.asyncio
async def test_parse_documents_office_calls_parse_only_with_pdf_ext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "含图素材.docx"
    src.write_bytes(b"PK\x03\x04dummy")
    dest = converted_pdf_path(src)
    dest.write_bytes(b"%PDF-1.4 converted")

    mat = types.SimpleNamespace(
        id=21,
        title="含图素材.docx",
        file_path=str(src),
        processing_status="processing",
        page_count=None,
    )
    service = types.SimpleNamespace(db=_AsyncNoopDB())
    seen: dict[str, object] = {}

    def _parse_only(file_path, ext, image_save_dir):
        seen["file_path"] = file_path
        seen["ext"] = ext
        return types.SimpleNamespace(page_count=3, file_type="pdf", images=[])

    convert = AsyncMock(return_value=dest)
    monkeypatch.setattr(
        "app.core.convert.office_to_pdf.convert_office_to_pdf", convert,
    )
    extractor = types.SimpleNamespace(parse_only=_parse_only)

    parse_results, failed = await phase1_parse.parse_documents(
        service, [mat], extractor, project_id=7,
    )

    assert failed == 0
    assert 21 in parse_results
    assert seen["ext"] == ".pdf"
    assert seen["file_path"] == str(dest)
    assert mat.file_path == str(dest)
    assert mat.title == "含图素材.docx"
    assert mat.page_count == 3


def test_parse_only_rejects_unnormalized_office() -> None:
    extractor = MaterialContentExtractor(enable_vl=False)
    with pytest.raises(ValueError, match="normalize_office_path_for_parse"):
        extractor.parse_only("dummy.docx", ".docx")
    with pytest.raises(ValueError, match="normalize_office_path_for_parse"):
        extractor.parse_only("dummy.doc", ".doc")


@pytest.mark.asyncio
async def test_knowledge_atomizer_normalizes_docx_before_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "知识库素材.docx"
    src.write_bytes(b"PK\x03\x04dummy")
    dest = converted_pdf_path(src)
    dest.write_bytes(b"%PDF-1.4 kb")

    convert = AsyncMock(return_value=dest)
    monkeypatch.setattr(
        "app.core.convert.office_to_pdf.convert_office_to_pdf", convert,
    )

    seen: dict[str, object] = {}

    class _FakeExtractor:
        def __init__(self, enable_vl: bool = True) -> None:
            self.enable_vl = enable_vl

        def parse_only(self, file_path, ext, image_save_dir):
            seen["file_path"] = file_path
            seen["ext"] = ext
            return types.SimpleNamespace(
                images=[],
                raw_text="正文",
                page_count=2,
                page_texts=[(1, "正文")],
                parsed_pages=[],
                file_type="pdf",
                tables=[],
            )

        async def batch_vl_recognize(self, images, project_id=None):
            return None

        def assemble_with_vl(self, pr):
            return types.SimpleNamespace(
                text=pr.raw_text,
                page_texts=pr.page_texts,
                page_count=pr.page_count,
                parsed_pages=pr.parsed_pages,
            )

    monkeypatch.setattr(
        "app.core.writer.material_content_extractor.MaterialContentExtractor",
        _FakeExtractor,
    )

    result = await KnowledgeAtomizer(enable_vl=False).atomize(
        file_path=src,
        document_id=9,
        image_save_dir=tmp_path / "images",
        title="知识库素材.docx",
        business_type="company_profile",
    )

    assert seen["ext"] == ".pdf"
    assert seen["file_path"] == str(dest)
    assert result.resolved_file_path == str(dest)
    assert result.full_text == "正文"
    convert.assert_awaited_once()
