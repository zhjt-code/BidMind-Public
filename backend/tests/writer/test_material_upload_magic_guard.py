"""写标素材上传：仅 PDF/Word 校验魔数，图片扩展名不误杀。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import settings
from app.core.exceptions import ServiceError
from app.services.writing.material_service import MaterialService


class _SavedDb:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        obj.id = 41


async def _resolve_project(project_id: int, _user: Any) -> SimpleNamespace:
    return SimpleNamespace(id=project_id)


def _service(db: _SavedDb) -> MaterialService:
    async def _noop(_material: Any) -> None:
        return None

    return MaterialService(
        db,  # type: ignore[arg-type]
        project_resolver=_resolve_project,
        refresh_material_title_derivatives_hook=_noop,
    )


@pytest.mark.asyncio
async def test_pdf_extension_with_zip_bytes_is_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    service = _service(_SavedDb())

    with pytest.raises(ServiceError) as exc:
        await service.save_material(5, SimpleNamespace(id=1), "bid.pdf", b"PK\x03\x04fake")

    assert "扩展名不符" in exc.value.detail
    assert not (tmp_path / "5" / "materials").exists()


@pytest.mark.asyncio
async def test_docx_extension_with_pdf_bytes_is_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同形态第二例：Word 扩展名配 PDF 内容同样拒绝。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    service = _service(_SavedDb())

    with pytest.raises(ServiceError) as exc:
        await service.save_material(6, SimpleNamespace(id=2), "plan.docx", b"%PDF-1.7")

    assert "扩展名不符" in exc.value.detail


@pytest.mark.asyncio
async def test_real_pdf_and_png_extension_are_accepted(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    db = _SavedDb()
    service = _service(db)

    pdf = await service.save_material(
        7, SimpleNamespace(id=1), "spec.pdf", b"%PDF-1.4 ok"
    )
    png = await service.save_material(
        7, SimpleNamespace(id=1), "photo.png", b"\x89PNG not-sniffed"
    )

    assert pdf.file_path.endswith("spec.pdf")
    assert png.file_path.endswith("photo.png")
    assert len(db.added) == 2
