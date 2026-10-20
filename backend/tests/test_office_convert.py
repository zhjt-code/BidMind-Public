"""Office → PDF 格式归一化防御测试。

覆盖问题形态「上传格式与页级 PDF 契约不一致」:
- 魔数表驱动（.pdf / .docx / .doc 三实体）
- 转换幂等、URL 未配置 / HTTP 失败 fail-visible
- convert_stage 对 PDF 直通、对 Word 回写 file_path
- 删除文档时清理转换配对文件
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.config import settings  # noqa: E402
from app.core.convert.office_to_pdf import (  # noqa: E402
    OFFICE_CONVERT_TIMEOUT_USER_MESSAGE,
    convert_office_to_pdf,
    converted_pdf_path,
    office_convert_siblings,
    sniff_upload_magic,
)
from app.core.tasks.process.convert_stage import run_convert_stage  # noqa: E402
from app.services.document_service import DocumentService  # noqa: E402


OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def test_sniff_upload_magic_covers_pdf_docx_and_doc() -> None:
    assert sniff_upload_magic(b"%PDF-1.4 x", ".pdf") is True
    assert sniff_upload_magic(b"PK\x03\x04xxxx", ".docx") is True
    assert sniff_upload_magic(OLE_MAGIC + b"x", ".doc") is True


def test_sniff_upload_magic_rejects_cross_entity_and_unknown() -> None:
    assert sniff_upload_magic(b"PK\x03\x04xxxx", ".pdf") is False
    assert sniff_upload_magic(b"%PDF-1.4 x", ".docx") is False
    assert sniff_upload_magic(b"PK\x03\x04xxxx", ".doc") is False
    assert sniff_upload_magic(b"hello", ".txt") is False


def test_office_convert_siblings_are_bidirectional() -> None:
    assert office_convert_siblings("tender_a.docx") == [Path("tender_a.docx.pdf")]
    assert office_convert_siblings("bid_b.doc") == [Path("bid_b.doc.pdf")]
    assert office_convert_siblings("tender_a.docx.pdf") == [Path("tender_a.docx")]
    assert office_convert_siblings("native.pdf") == []


@pytest.mark.asyncio
async def test_convert_office_to_pdf_is_idempotent(tmp_path: Path) -> None:
    src = tmp_path / "a.docx"
    src.write_bytes(b"PK\x03\x04dummy")
    dest = converted_pdf_path(src)
    dest.write_bytes(b"%PDF-1.4 already-converted")

    result = await convert_office_to_pdf(src)
    assert result == dest
    assert dest.read_bytes() == b"%PDF-1.4 already-converted"


@pytest.mark.asyncio
async def test_convert_office_to_pdf_fails_visible_when_url_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "a.docx"
    src.write_bytes(b"PK\x03\x04dummy")
    monkeypatch.setattr(settings, "OFFICE_CONVERT_URL", "")

    with pytest.raises(ValueError, match="未配置"):
        await convert_office_to_pdf(src)


@pytest.mark.asyncio
async def test_convert_office_to_pdf_writes_pdf_on_http_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "a.doc"
    src.write_bytes(OLE_MAGIC + b"dummy")
    monkeypatch.setattr(settings, "OFFICE_CONVERT_URL", "http://gotenberg.test")

    class _FakeResponse:
        status_code = 200
        content = b"%PDF-1.4 converted"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, files=None):
            assert url.endswith("/forms/libreoffice/convert")
            return _FakeResponse()

    monkeypatch.setattr(
        "app.core.convert.office_to_pdf.httpx.AsyncClient", _FakeClient
    )
    dest = await convert_office_to_pdf(src, timeout_sec=5)
    assert dest == converted_pdf_path(src)
    assert dest.read_bytes().startswith(b"%PDF")


@pytest.mark.asyncio
async def test_convert_office_to_pdf_fails_visible_on_http_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "a.docx"
    src.write_bytes(b"PK\x03\x04dummy")
    monkeypatch.setattr(settings, "OFFICE_CONVERT_URL", "http://gotenberg.test")

    class _FakeResponse:
        status_code = 500
        content = b"boom"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, files=None):
            return _FakeResponse()

    monkeypatch.setattr(
        "app.core.convert.office_to_pdf.httpx.AsyncClient", _FakeClient
    )
    with pytest.raises(ValueError, match="转换失败"):
        await convert_office_to_pdf(src)


def _fake_http_client(status_code: int, exc: Exception | None = None):
    class _FakeResponse:
        def __init__(self) -> None:
            self.status_code = status_code
            self.content = b"boom"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, files=None):
            if exc is not None:
                raise exc
            return _FakeResponse()

    return _FakeClient


@pytest.mark.asyncio
async def test_convert_office_to_pdf_maps_http_503_to_timeout_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "tech.docx"
    src.write_bytes(b"PK\x03\x04dummy")
    monkeypatch.setattr(settings, "OFFICE_CONVERT_URL", "http://gotenberg.test")
    monkeypatch.setattr(
        "app.core.convert.office_to_pdf.httpx.AsyncClient",
        _fake_http_client(503),
    )
    with pytest.raises(ValueError, match="超过 3 分钟") as caught:
        await convert_office_to_pdf(src)
    assert str(caught.value) == OFFICE_CONVERT_TIMEOUT_USER_MESSAGE
    assert "HTTP 503" not in str(caught.value)


@pytest.mark.asyncio
async def test_convert_office_to_pdf_maps_client_timeout_to_same_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "commercial.doc"
    src.write_bytes(OLE_MAGIC + b"dummy")
    monkeypatch.setattr(settings, "OFFICE_CONVERT_URL", "http://gotenberg.test")
    monkeypatch.setattr(
        "app.core.convert.office_to_pdf.httpx.AsyncClient",
        _fake_http_client(200, exc=httpx.TimeoutException("deadline")),
    )
    with pytest.raises(ValueError, match="超过 3 分钟") as caught:
        await convert_office_to_pdf(src)
    assert str(caught.value) == OFFICE_CONVERT_TIMEOUT_USER_MESSAGE


def test_gotenberg_api_timeout_is_180s_on_local_and_prod_compose() -> None:
    root = Path(__file__).resolve().parents[2]
    local = (root / "docker-compose.yml").read_text(encoding="utf-8")
    prod = (root / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "API_TIMEOUT" in local and "180s" in local
    assert "API_TIMEOUT=180s" in prod


@pytest.mark.asyncio
async def test_run_convert_stage_passthrough_for_pdf() -> None:
    called = {"n": 0}

    def _heartbeat(*_args, **_kwargs) -> None:
        called["n"] += 1

    result = await run_convert_stage(
        file_path="demo.pdf",
        document_id=1,
        heartbeat=_heartbeat,
    )
    assert result == "demo.pdf"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_run_convert_stage_updates_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "tender.docx"
    src.write_bytes(b"PK\x03\x04x")
    dest = converted_pdf_path(src)

    async def _fake_convert(path, timeout_sec=None):
        dest.write_bytes(b"%PDF-1.4 x")
        return dest

    document = SimpleNamespace(file_path=str(src), project_id=9, id=3)
    committed: list[bool] = []

    class _Db:
        async def commit(self) -> None:
            committed.append(True)

    @asynccontextmanager
    async def _factory():
        yield _Db()

    recorded: list[dict] = []

    async def _fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        "app.core.tasks.process.convert_stage.convert_office_to_pdf",
        _fake_convert,
    )
    monkeypatch.setattr(
        "app.core.tasks.process_task.async_session_factory", _factory
    )
    monkeypatch.setattr(
        "app.core.tasks.process_task._get_document",
        AsyncMock(return_value=document),
    )
    monkeypatch.setattr(
        "app.core.tasks.process.convert_stage.record_stage", _fake_record
    )

    result = await run_convert_stage(
        file_path=str(src),
        document_id=3,
        heartbeat=lambda *_args, **_kwargs: None,
    )
    assert result == str(dest)
    assert document.file_path == str(dest)
    assert committed == [True]
    assert recorded[0]["stage"] == "office_convert"


@pytest.mark.asyncio
async def test_delete_document_cleans_convert_sibling(tmp_path: Path) -> None:
    original = tmp_path / "bid_1.docx"
    converted = converted_pdf_path(original)
    original.write_bytes(b"PK\x03\x04x")
    converted.write_bytes(b"%PDF-1.4 x")

    class _Db:
        def __init__(self) -> None:
            self.deleted = None

        async def delete(self, obj) -> None:
            self.deleted = obj

        async def flush(self) -> None:
            return None

    service = DocumentService.__new__(DocumentService)
    service.db = _Db()
    service.upload_dir = tmp_path
    document = SimpleNamespace(
        file_path=str(converted),
        filename="bid_1.docx",
        project_id=1,
        id=8,
    )

    await service._delete_document(document)

    assert not converted.exists()
    assert not original.exists()
    assert service.db.deleted is document
